# -*- coding: utf-8 -*-
import warnings

import numpy as np
from pytz import utc

from pandas._libs import tslib
from pandas._libs.tslib import Timestamp, NaT, iNaT
from pandas._libs.tslibs import (
    conversion, fields, timezones,
    resolution as libresolution)

from pandas.util._decorators import cache_readonly

from pandas.core.dtypes.common import (
    _NS_DTYPE,
    is_datetime64tz_dtype,
    is_datetime64_dtype,
    _ensure_int64)
from pandas.core.dtypes.dtypes import DatetimeTZDtype

from pandas.tseries.frequencies import to_offset, DateOffset

from .datetimelike import DatetimeLikeArrayMixin


def _field_accessor(name, field, docstring=None):
    def f(self):
        values = self.asi8
        if self.tz is not None:
            if self.tz is not utc:
                values = self._local_timestamps()

        if field in self._bool_ops:
            if field.endswith(('start', 'end')):
                freq = self.freq
                month_kw = 12
                if freq:
                    kwds = freq.kwds
                    month_kw = kwds.get('startingMonth', kwds.get('month', 12))

                result = fields.get_start_end_field(values, field,
                                                    self.freqstr, month_kw)
            else:
                result = fields.get_date_field(values, field)

            # these return a boolean by-definition
            return result

        if field in self._object_ops:
            result = fields.get_date_name_field(values, field)
            result = self._maybe_mask_results(result)

        else:
            result = fields.get_date_field(values, field)
            result = self._maybe_mask_results(result, convert='float64')

        return result

    f.__name__ = name
    f.__doc__ = docstring
    return property(f)


class DatetimeArrayMixin(DatetimeLikeArrayMixin):
    """
    Assumes that subclass __new__/__init__ defines:
        tz
        _freq
        _data
    """
    _bool_ops = ['is_month_start', 'is_month_end',
                 'is_quarter_start', 'is_quarter_end', 'is_year_start',
                 'is_year_end', 'is_leap_year']
    _object_ops = ['weekday_name', 'freq', 'tz']

    # -----------------------------------------------------------------
    # Constructors

    _attributes = ["freq", "tz"]

    @classmethod
    def _simple_new(cls, values, freq=None, tz=None, **kwargs):
        """
        we require the we have a dtype compat for the values
        if we are passed a non-dtype compat, then coerce using the constructor
        """

        if getattr(values, 'dtype', None) is None:
            # empty, but with dtype compat
            if values is None:
                values = np.empty(0, dtype=_NS_DTYPE)
                return cls(values, freq=freq, tz=tz, **kwargs)
            values = np.array(values, copy=False)

        if not is_datetime64_dtype(values):
            values = _ensure_int64(values).view(_NS_DTYPE)

        result = object.__new__(cls)
        result._data = values
        result._freq = freq
        tz = timezones.maybe_get_tz(tz)
        result._tz = timezones.tz_standardize(tz)
        return result

    def __new__(cls, values, freq=None, tz=None):
        if (freq is not None and not isinstance(freq, DateOffset) and
                freq != 'infer'):
            freq = to_offset(freq)

        result = cls._simple_new(values, freq=freq, tz=tz)
        if freq == 'infer':
            inferred = result.inferred_freq
            if inferred:
                result.freq = to_offset(inferred)

        # NB: Among other things not yet ported from the DatetimeIndex
        # constructor, this does not call _deepcopy_if_needed
        return result

    # -----------------------------------------------------------------
    # Descriptive Properties

    @property
    def _box_func(self):
        return lambda x: Timestamp(x, freq=self.freq, tz=self.tz)

    @cache_readonly
    def dtype(self):
        if self.tz is None:
            return _NS_DTYPE
        return DatetimeTZDtype('ns', self.tz)

    @property
    def tzinfo(self):
        """
        Alias for tz attribute
        """
        return self.tz

    @property  # NB: override with cache_readonly in immutable subclasses
    def _timezone(self):
        """ Comparable timezone both for pytz / dateutil"""
        return timezones.get_timezone(self.tzinfo)

    @property
    def offset(self):
        """get/set the frequency of the instance"""
        msg = ('{cls}.offset has been deprecated and will be removed '
               'in a future version; use {cls}.freq instead.'
               .format(cls=type(self).__name__))
        warnings.warn(msg, FutureWarning, stacklevel=2)
        return self.freq

    @offset.setter
    def offset(self, value):
        """get/set the frequency of the instance"""
        msg = ('{cls}.offset has been deprecated and will be removed '
               'in a future version; use {cls}.freq instead.'
               .format(cls=type(self).__name__))
        warnings.warn(msg, FutureWarning, stacklevel=2)
        self.freq = value

    @property  # NB: override with cache_readonly in immutable subclasses
    def is_normalized(self):
        """
        Returns True if all of the dates are at midnight ("no time")
        """
        return conversion.is_date_array_normalized(self.asi8, self.tz)

    @property  # NB: override with cache_readonly in immutable subclasses
    def _resolution(self):
        return libresolution.resolution(self.asi8, self.tz)

    # ----------------------------------------------------------------
    # Array-like Methods

    def __iter__(self):
        """
        Return an iterator over the boxed values

        Yields
        -------
        tstamp : Timestamp
        """

        # convert in chunks of 10k for efficiency
        data = self.asi8
        length = len(self)
        chunksize = 10000
        chunks = int(length / chunksize) + 1
        for i in range(chunks):
            start_i = i * chunksize
            end_i = min((i + 1) * chunksize, length)
            converted = tslib.ints_to_pydatetime(data[start_i:end_i],
                                                 tz=self.tz, freq=self.freq,
                                                 box="timestamp")
            for v in converted:
                yield v

    # -----------------------------------------------------------------
    # Comparison Methods

    def _has_same_tz(self, other):
        zzone = self._timezone

        # vzone sholdn't be None if value is non-datetime like
        if isinstance(other, np.datetime64):
            # convert to Timestamp as np.datetime64 doesn't have tz attr
            other = Timestamp(other)
        vzone = timezones.get_timezone(getattr(other, 'tzinfo', '__no_tz__'))
        return zzone == vzone

    def _assert_tzawareness_compat(self, other):
        # adapted from _Timestamp._assert_tzawareness_compat
        other_tz = getattr(other, 'tzinfo', None)
        if is_datetime64tz_dtype(other):
            # Get tzinfo from Series dtype
            other_tz = other.dtype.tz
        if other is NaT:
            # pd.NaT quacks both aware and naive
            pass
        elif self.tz is None:
            if other_tz is not None:
                raise TypeError('Cannot compare tz-naive and tz-aware '
                                'datetime-like objects.')
        elif other_tz is None:
            raise TypeError('Cannot compare tz-naive and tz-aware '
                            'datetime-like objects')

    # -----------------------------------------------------------------
    # Arithmetic Methods

    def _sub_datelike_dti(self, other):
        """subtraction of two DatetimeIndexes"""
        if not len(self) == len(other):
            raise ValueError("cannot add indices of unequal length")

        self_i8 = self.asi8
        other_i8 = other.asi8
        new_values = self_i8 - other_i8
        if self.hasnans or other.hasnans:
            mask = (self._isnan) | (other._isnan)
            new_values[mask] = iNaT
        return new_values.view('timedelta64[ns]')

    # -----------------------------------------------------------------
    # Timezone Conversion and Localization Methods

    def _local_timestamps(self):
        """
        Convert to an i8 (unix-like nanosecond timestamp) representation
        while keeping the local timezone and not using UTC.
        This is used to calculate time-of-day information as if the timestamps
        were timezone-naive.
        """
        values = self.asi8
        indexer = values.argsort()
        result = conversion.tz_convert(values.take(indexer), utc, self.tz)

        n = len(indexer)
        reverse = np.empty(n, dtype=np.int_)
        reverse.put(indexer, np.arange(n))
        return result.take(reverse)

    def tz_convert(self, tz):
        """
        Convert tz-aware Datetime Array/Index from one time zone to another.

        Parameters
        ----------
        tz : string, pytz.timezone, dateutil.tz.tzfile or None
            Time zone for time. Corresponding timestamps would be converted
            to this time zone of the Datetime Array/Index. A `tz` of None will
            convert to UTC and remove the timezone information.

        Returns
        -------
        normalized : same type as self

        Raises
        ------
        TypeError
            If Datetime Array/Index is tz-naive.

        See Also
        --------
        DatetimeIndex.tz : A timezone that has a variable offset from UTC
        DatetimeIndex.tz_localize : Localize tz-naive DatetimeIndex to a
            given time zone, or remove timezone from a tz-aware DatetimeIndex.

        Examples
        --------
        With the `tz` parameter, we can change the DatetimeIndex
        to other time zones:

        >>> dti = pd.DatetimeIndex(start='2014-08-01 09:00',
        ...                        freq='H', periods=3, tz='Europe/Berlin')

        >>> dti
        DatetimeIndex(['2014-08-01 09:00:00+02:00',
                       '2014-08-01 10:00:00+02:00',
                       '2014-08-01 11:00:00+02:00'],
                      dtype='datetime64[ns, Europe/Berlin]', freq='H')

        >>> dti.tz_convert('US/Central')
        DatetimeIndex(['2014-08-01 02:00:00-05:00',
                       '2014-08-01 03:00:00-05:00',
                       '2014-08-01 04:00:00-05:00'],
                      dtype='datetime64[ns, US/Central]', freq='H')

        With the ``tz=None``, we can remove the timezone (after converting
        to UTC if necessary):

        >>> dti = pd.DatetimeIndex(start='2014-08-01 09:00',freq='H',
        ...                        periods=3, tz='Europe/Berlin')

        >>> dti
        DatetimeIndex(['2014-08-01 09:00:00+02:00',
                       '2014-08-01 10:00:00+02:00',
                       '2014-08-01 11:00:00+02:00'],
                        dtype='datetime64[ns, Europe/Berlin]', freq='H')

        >>> dti.tz_convert(None)
        DatetimeIndex(['2014-08-01 07:00:00',
                       '2014-08-01 08:00:00',
                       '2014-08-01 09:00:00'],
                        dtype='datetime64[ns]', freq='H')
        """
        tz = timezones.maybe_get_tz(tz)

        if self.tz is None:
            # tz naive, use tz_localize
            raise TypeError('Cannot convert tz-naive timestamps, use '
                            'tz_localize to localize')

        # No conversion since timestamps are all UTC to begin with
        return self._shallow_copy(tz=tz)

    def tz_localize(self, tz, ambiguous='raise', errors='raise'):
        """
        Localize tz-naive Datetime Array/Index to tz-aware
        Datetime Array/Index.

        This method takes a time zone (tz) naive Datetime Array/Index object
        and makes this time zone aware. It does not move the time to another
        time zone.
        Time zone localization helps to switch from time zone aware to time
        zone unaware objects.

        Parameters
        ----------
        tz : string, pytz.timezone, dateutil.tz.tzfile or None
            Time zone to convert timestamps to. Passing ``None`` will
            remove the time zone information preserving local time.
        ambiguous : str {'infer', 'NaT', 'raise'} or bool array,
            default 'raise'

            - 'infer' will attempt to infer fall dst-transition hours based on
              order
            - bool-ndarray where True signifies a DST time, False signifies a
              non-DST time (note that this flag is only applicable for
              ambiguous times)
            - 'NaT' will return NaT where there are ambiguous times
            - 'raise' will raise an AmbiguousTimeError if there are ambiguous
              times

        errors : {'raise', 'coerce'}, default 'raise'

            - 'raise' will raise a NonExistentTimeError if a timestamp is not
              valid in the specified time zone (e.g. due to a transition from
              or to DST time)
            - 'coerce' will return NaT if the timestamp can not be converted
              to the specified time zone

            .. versionadded:: 0.19.0

        Returns
        -------
        result : same type as self
            Array/Index converted to the specified time zone.

        Raises
        ------
        TypeError
            If the Datetime Array/Index is tz-aware and tz is not None.

        See Also
        --------
        DatetimeIndex.tz_convert : Convert tz-aware DatetimeIndex from
            one time zone to another.

        Examples
        --------
        >>> tz_naive = pd.date_range('2018-03-01 09:00', periods=3)
        >>> tz_naive
        DatetimeIndex(['2018-03-01 09:00:00', '2018-03-02 09:00:00',
                       '2018-03-03 09:00:00'],
                      dtype='datetime64[ns]', freq='D')

        Localize DatetimeIndex in US/Eastern time zone:

        >>> tz_aware = tz_naive.tz_localize(tz='US/Eastern')
        >>> tz_aware
        DatetimeIndex(['2018-03-01 09:00:00-05:00',
                       '2018-03-02 09:00:00-05:00',
                       '2018-03-03 09:00:00-05:00'],
                      dtype='datetime64[ns, US/Eastern]', freq='D')

        With the ``tz=None``, we can remove the time zone information
        while keeping the local time (not converted to UTC):

        >>> tz_aware.tz_localize(None)
        DatetimeIndex(['2018-03-01 09:00:00', '2018-03-02 09:00:00',
                       '2018-03-03 09:00:00'],
                      dtype='datetime64[ns]', freq='D')
        """
        if self.tz is not None:
            if tz is None:
                new_dates = conversion.tz_convert(self.asi8, 'UTC', self.tz)
            else:
                raise TypeError("Already tz-aware, use tz_convert to convert.")
        else:
            tz = timezones.maybe_get_tz(tz)
            # Convert to UTC

            new_dates = conversion.tz_localize_to_utc(self.asi8, tz,
                                                      ambiguous=ambiguous,
                                                      errors=errors)
        new_dates = new_dates.view(_NS_DTYPE)
        return self._shallow_copy(new_dates, tz=tz)

    # ----------------------------------------------------------------
    # Conversion Methods - Vectorized analogues of Timestamp methods

    def to_pydatetime(self):
        """
        Return Datetime Array/Index as object ndarray of datetime.datetime
        objects

        Returns
        -------
        datetimes : ndarray
        """
        return tslib.ints_to_pydatetime(self.asi8, tz=self.tz)

    # -----------------------------------------------------------------
    # Properties - Vectorized Timestamp Properties/Methods

    def month_name(self, locale=None):
        """
        Return the month names of the DateTimeIndex with specified locale.

        Parameters
        ----------
        locale : string, default None (English locale)
            locale determining the language in which to return the month name

        Returns
        -------
        month_names : Index
            Index of month names

        .. versionadded:: 0.23.0
        """
        if self.tz is not None and self.tz is not utc:
            values = self._local_timestamps()
        else:
            values = self.asi8

        result = fields.get_date_name_field(values, 'month_name',
                                            locale=locale)
        result = self._maybe_mask_results(result)
        return result

    def day_name(self, locale=None):
        """
        Return the day names of the DateTimeIndex with specified locale.

        Parameters
        ----------
        locale : string, default None (English locale)
            locale determining the language in which to return the day name

        Returns
        -------
        month_names : Index
            Index of day names

        .. versionadded:: 0.23.0
        """
        if self.tz is not None and self.tz is not utc:
            values = self._local_timestamps()
        else:
            values = self.asi8

        result = fields.get_date_name_field(values, 'day_name',
                                            locale=locale)
        result = self._maybe_mask_results(result)
        return result

    @property
    def time(self):
        """
        Returns numpy array of datetime.time. The time part of the Timestamps.
        """
        # If the Timestamps have a timezone that is not UTC,
        # convert them into their i8 representation while
        # keeping their timezone and not using UTC
        if self.tz is not None and self.tz is not utc:
            timestamps = self._local_timestamps()
        else:
            timestamps = self.asi8

        return tslib.ints_to_pydatetime(timestamps, box="time")

    @property
    def date(self):
        """
        Returns numpy array of python datetime.date objects (namely, the date
        part of Timestamps without timezone information).
        """
        # If the Timestamps have a timezone that is not UTC,
        # convert them into their i8 representation while
        # keeping their timezone and not using UTC
        if self.tz is not None and self.tz is not utc:
            timestamps = self._local_timestamps()
        else:
            timestamps = self.asi8

        return tslib.ints_to_pydatetime(timestamps, box="date")

    year = _field_accessor('year', 'Y', "The year of the datetime")
    month = _field_accessor('month', 'M',
                            "The month as January=1, December=12")
    day = _field_accessor('day', 'D', "The days of the datetime")
    hour = _field_accessor('hour', 'h', "The hours of the datetime")
    minute = _field_accessor('minute', 'm', "The minutes of the datetime")
    second = _field_accessor('second', 's', "The seconds of the datetime")
    microsecond = _field_accessor('microsecond', 'us',
                                  "The microseconds of the datetime")
    nanosecond = _field_accessor('nanosecond', 'ns',
                                 "The nanoseconds of the datetime")
    weekofyear = _field_accessor('weekofyear', 'woy',
                                 "The week ordinal of the year")
    week = weekofyear
    dayofweek = _field_accessor('dayofweek', 'dow',
                                "The day of the week with Monday=0, Sunday=6")
    weekday = dayofweek

    weekday_name = _field_accessor(
        'weekday_name',
        'weekday_name',
        "The name of day in a week (ex: Friday)\n\n.. deprecated:: 0.23.0")

    dayofyear = _field_accessor('dayofyear', 'doy',
                                "The ordinal day of the year")
    quarter = _field_accessor('quarter', 'q', "The quarter of the date")
    days_in_month = _field_accessor(
        'days_in_month',
        'dim',
        "The number of days in the month")
    daysinmonth = days_in_month
    is_month_start = _field_accessor(
        'is_month_start',
        'is_month_start',
        "Logical indicating if first day of month (defined by frequency)")
    is_month_end = _field_accessor(
        'is_month_end',
        'is_month_end',
        """
        Indicator for whether the date is the last day of the month.

        Returns
        -------
        Series or array
            For Series, returns a Series with boolean values. For
            DatetimeIndex, returns a boolean array.

        See Also
        --------
        is_month_start : Indicator for whether the date is the first day
            of the month.

        Examples
        --------
        This method is available on Series with datetime values under
        the ``.dt`` accessor, and directly on DatetimeIndex.

        >>> dates = pd.Series(pd.date_range("2018-02-27", periods=3))
        >>> dates
        0   2018-02-27
        1   2018-02-28
        2   2018-03-01
        dtype: datetime64[ns]
        >>> dates.dt.is_month_end
        0    False
        1    True
        2    False
        dtype: bool

        >>> idx = pd.date_range("2018-02-27", periods=3)
        >>> idx.is_month_end
        array([False,  True, False], dtype=bool)
        """)
    is_quarter_start = _field_accessor(
        'is_quarter_start',
        'is_quarter_start',
        """
        Indicator for whether the date is the first day of a quarter.

        Returns
        -------
        is_quarter_start : Series or DatetimeIndex
            The same type as the original data with boolean values. Series will
            have the same name and index. DatetimeIndex will have the same
            name.

        See Also
        --------
        quarter : Return the quarter of the date.
        is_quarter_end : Similar property for indicating the quarter start.

        Examples
        --------
        This method is available on Series with datetime values under
        the ``.dt`` accessor, and directly on DatetimeIndex.

        >>> df = pd.DataFrame({'dates': pd.date_range("2017-03-30",
        ...                   periods=4)})
        >>> df.assign(quarter=df.dates.dt.quarter,
        ...           is_quarter_start=df.dates.dt.is_quarter_start)
               dates  quarter  is_quarter_start
        0 2017-03-30        1             False
        1 2017-03-31        1             False
        2 2017-04-01        2              True
        3 2017-04-02        2             False

        >>> idx = pd.date_range('2017-03-30', periods=4)
        >>> idx
        DatetimeIndex(['2017-03-30', '2017-03-31', '2017-04-01', '2017-04-02'],
                      dtype='datetime64[ns]', freq='D')

        >>> idx.is_quarter_start
        array([False, False,  True, False])
        """)
    is_quarter_end = _field_accessor(
        'is_quarter_end',
        'is_quarter_end',
        """
        Indicator for whether the date is the last day of a quarter.

        Returns
        -------
        is_quarter_end : Series or DatetimeIndex
            The same type as the original data with boolean values. Series will
            have the same name and index. DatetimeIndex will have the same
            name.

        See Also
        --------
        quarter : Return the quarter of the date.
        is_quarter_start : Similar property indicating the quarter start.

        Examples
        --------
        This method is available on Series with datetime values under
        the ``.dt`` accessor, and directly on DatetimeIndex.

        >>> df = pd.DataFrame({'dates': pd.date_range("2017-03-30",
        ...                    periods=4)})
        >>> df.assign(quarter=df.dates.dt.quarter,
        ...           is_quarter_end=df.dates.dt.is_quarter_end)
               dates  quarter    is_quarter_end
        0 2017-03-30        1             False
        1 2017-03-31        1              True
        2 2017-04-01        2             False
        3 2017-04-02        2             False

        >>> idx = pd.date_range('2017-03-30', periods=4)
        >>> idx
        DatetimeIndex(['2017-03-30', '2017-03-31', '2017-04-01', '2017-04-02'],
                      dtype='datetime64[ns]', freq='D')

        >>> idx.is_quarter_end
        array([False,  True, False, False])
        """)
    is_year_start = _field_accessor(
        'is_year_start',
        'is_year_start',
        """
        Indicate whether the date is the first day of a year.

        Returns
        -------
        Series or DatetimeIndex
            The same type as the original data with boolean values. Series will
            have the same name and index. DatetimeIndex will have the same
            name.

        See Also
        --------
        is_year_end : Similar property indicating the last day of the year.

        Examples
        --------
        This method is available on Series with datetime values under
        the ``.dt`` accessor, and directly on DatetimeIndex.

        >>> dates = pd.Series(pd.date_range("2017-12-30", periods=3))
        >>> dates
        0   2017-12-30
        1   2017-12-31
        2   2018-01-01
        dtype: datetime64[ns]

        >>> dates.dt.is_year_start
        0    False
        1    False
        2    True
        dtype: bool

        >>> idx = pd.date_range("2017-12-30", periods=3)
        >>> idx
        DatetimeIndex(['2017-12-30', '2017-12-31', '2018-01-01'],
                      dtype='datetime64[ns]', freq='D')

        >>> idx.is_year_start
        array([False, False,  True])
        """)
    is_year_end = _field_accessor(
        'is_year_end',
        'is_year_end',
        """
        Indicate whether the date is the last day of the year.

        Returns
        -------
        Series or DatetimeIndex
            The same type as the original data with boolean values. Series will
            have the same name and index. DatetimeIndex will have the same
            name.

        See Also
        --------
        is_year_start : Similar property indicating the start of the year.

        Examples
        --------
        This method is available on Series with datetime values under
        the ``.dt`` accessor, and directly on DatetimeIndex.

        >>> dates = pd.Series(pd.date_range("2017-12-30", periods=3))
        >>> dates
        0   2017-12-30
        1   2017-12-31
        2   2018-01-01
        dtype: datetime64[ns]

        >>> dates.dt.is_year_end
        0    False
        1     True
        2    False
        dtype: bool

        >>> idx = pd.date_range("2017-12-30", periods=3)
        >>> idx
        DatetimeIndex(['2017-12-30', '2017-12-31', '2018-01-01'],
                      dtype='datetime64[ns]', freq='D')

        >>> idx.is_year_end
        array([False,  True, False])
        """)
    is_leap_year = _field_accessor(
        'is_leap_year',
        'is_leap_year',
        """
        Boolean indicator if the date belongs to a leap year.

        A leap year is a year, which has 366 days (instead of 365) including
        29th of February as an intercalary day.
        Leap years are years which are multiples of four with the exception
        of years divisible by 100 but not by 400.

        Returns
        -------
        Series or ndarray
             Booleans indicating if dates belong to a leap year.

        Examples
        --------
        This method is available on Series with datetime values under
        the ``.dt`` accessor, and directly on DatetimeIndex.

        >>> idx = pd.date_range("2012-01-01", "2015-01-01", freq="Y")
        >>> idx
        DatetimeIndex(['2012-12-31', '2013-12-31', '2014-12-31'],
                      dtype='datetime64[ns]', freq='A-DEC')
        >>> idx.is_leap_year
        array([ True, False, False], dtype=bool)

        >>> dates = pd.Series(idx)
        >>> dates_series
        0   2012-12-31
        1   2013-12-31
        2   2014-12-31
        dtype: datetime64[ns]
        >>> dates_series.dt.is_leap_year
        0     True
        1    False
        2    False
        dtype: bool
        """)

    def to_julian_date(self):
        """
        Convert Datetime Array to float64 ndarray of Julian Dates.
        0 Julian date is noon January 1, 4713 BC.
        http://en.wikipedia.org/wiki/Julian_day
        """

        # http://mysite.verizon.net/aesir_research/date/jdalg2.htm
        year = np.asarray(self.year)
        month = np.asarray(self.month)
        day = np.asarray(self.day)
        testarr = month < 3
        year[testarr] -= 1
        month[testarr] += 12
        return (day +
                np.fix((153 * month - 457) / 5) +
                365 * year +
                np.floor(year / 4) -
                np.floor(year / 100) +
                np.floor(year / 400) +
                1721118.5 +
                (self.hour +
                 self.minute / 60.0 +
                 self.second / 3600.0 +
                 self.microsecond / 3600.0 / 1e+6 +
                 self.nanosecond / 3600.0 / 1e+9
                 ) / 24.0)
