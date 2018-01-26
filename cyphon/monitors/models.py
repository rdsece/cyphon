# -*- coding: utf-8 -*-
# Copyright 2017-2018 Dunbar Security Solutions, Inc.
#
# This file is part of Cyphon Engine.
#
# Cyphon Engine is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# Cyphon Engine is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Cyphon Engine. If not, see <http://www.gnu.org/licenses/>.
"""
Defines a Monitor class for monitoring the rate at which data is saved
to Distilleries. Monitors can generate Alerts if data is not being saved
at the expected rate.
"""

# standard library
from datetime import timedelta
import json

# third party
from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

# local
from alarms.models import Alarm, AlarmManager
from alerts.models import Alert
from cyphon.choices import (
    ALERT_LEVEL_CHOICES,
    MONITOR_STATUS_CHOICES,
    TIME_UNIT_CHOICES,
)
from cyphon.fieldsets import QueryFieldset
from distilleries.models import Distillery
from engines.queries import EngineQuery
from engines.sorter import SortParam, Sorter
import utils.dateutils.dateutils as dt


class MonitorManager(AlarmManager):
    """

    """

    def find_relevant(self, distillery):
        """

        """
        active_monitors = self.find_enabled()
        return active_monitors.filter(distilleries=distillery)


class Monitor(Alarm):
    """
    A Monitor monitors one or more Distilleries for saved data.
    It can be used to generate Alerts if data is not being saved
    to the Distilleries at an expected rate.

    Attributes
    ----------
    name : str
        The name of the |Monitor|.

    enabled : bool
        If True, the Monitor will be included in Monitor status updates.

    distilleries : Distilleries
        One or more Distilleries that the Monitor should watch.

    time_interval : int
        Maximum length of time that the Monitor's Distilleries can have
        no activity before the Monitor status changes to unhealthy.

    time_unit : str
        The time units for the time_interval. Possible values are
        constrained to |TIME_UNIT_CHOICES|.

    alerts_enabled : bool
        If True, the Monitor is allowed to generate Alerts.

    repeating_alerts : bool
        If True, the Monitor will generate an Alert at every time
        interval when its status is unhealthy. If False, the Monitor
        will only generate an Alert when its status changes from healthy
        to unhealthy.

    alert_level : str
        The level to use when generating Alerts. Possible values are
        constrained to MONITOR_STATUS_CHOICES.

    last_alert_date : datetime
        A |datetime| indicating the created_date for the last Alert
        generated by the monitor.

    last_alert_id : int
        A positive integer indicating the id of the last Alert
        generated by the monitor.

    status : str
        The current status of the Monitor. Possible values are
        constrained to MONITOR_STATUS_CHOICES.

    created_date : datetime
        A |datetime| indicating when the Monitor was created.

    last_updated : datetime
        A |datetime| indicating when the Monitor status was last
        updated (though the status may not have changed).

    last_healthy : datetime
        A |datetime| indicating when the Monitor last had a healthy
        status.

    last_active_distillery : Distillery
        The last Distillery that was saved to among the Distilleries
        being monitored.

    last_saved_doc : str
        The document id of the last document that was saved among the
        Distilleries being monitored.

    """
    distilleries = models.ManyToManyField(
        Distillery,
        related_name='+',  # do not create backwards relation
    )
    time_interval = models.IntegerField()
    time_unit = models.CharField(max_length=3, choices=TIME_UNIT_CHOICES)
    alerts_enabled = models.BooleanField(default=True)
    repeating_alerts = models.BooleanField(default=False)
    alert_level = models.CharField(
        max_length=20,
        choices=ALERT_LEVEL_CHOICES
    )
    last_alert_date = models.DateTimeField(blank=True, null=True)
    last_alert_id = models.PositiveIntegerField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=MONITOR_STATUS_CHOICES
    )
    created_date = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    last_healthy = models.DateTimeField(blank=True, null=True)
    last_active_distillery = models.ForeignKey(
        Distillery,
        blank=True,
        null=True,
        verbose_name=_('distillery')
    )
    last_saved_doc = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('document id')
    )

    _HEALTHY = 'GREEN'
    _UNHEALTHY = 'RED'

    objects = MonitorManager()

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """
        Overrides the save() method to validate distilleries and update
        the status of the Monitor.
        """
        self._update_fields()
        super(Monitor, self).save(*args, **kwargs)

    def _get_interval_in_seconds(self):
        """
        Returns the number of seconds in the Monitor's time interval.
        """
        return dt.convert_time_to_seconds(self.time_interval, self.time_unit)

    def _get_inactive_seconds(self):
        """
        Returns the number of seconds since a document was saved to one
        of the Monitor's distilleries.
        """
        if self.last_healthy is not None or self.created_date is not None:
            if self.last_healthy is not None:
                time_delta = timezone.now() - self.last_healthy
            else:
                time_delta = timezone.now() - self.created_date
            return time_delta.total_seconds()
        else:
            return 0

    def _get_last_alert_seconds(self):
        """
        Returns the number of seconds since the Monitor last generated
        an Alert.
        """
        if self.last_alert_date is not None:
            time_delta = timezone.now() - self.last_alert_date
            return time_delta.total_seconds()

    def _get_inactive_interval(self):
        """
        Returns a string with the approximate time since a document was
        saved to one of the Monitor's distilleries (e.g., '35 s', '6 m',
        '2 h', '1 d'). The time is rounded down to the nearest integer.
        """
        seconds = self._get_inactive_seconds()
        return dt.convert_seconds(seconds)

    def _is_overdue(self):
        """
        Returns a Boolean indicating whether the time since a document
        was last saved to one of the Monitor's distilleries exceeds the
        Monitor's interval.
        """
        return self._get_inactive_seconds() > self._get_interval_in_seconds()

    def _get_interval_start(self):
        """
        Returns a DateTime representing the start of the monitoring
        interval.
        """
        seconds = self._get_interval_in_seconds()
        return timezone.now() - timedelta(seconds=seconds)

    def _get_query_start_time(self):
        """
        Returns either the last_healthy datetime or the start of the
        monitoring interval, whichever is older.
        """
        interval_start = self._get_interval_start()
        if self.last_healthy and self.last_healthy < interval_start:
            return self.last_healthy
        else:
            return interval_start

    def _get_query(self, date_field):
        """
        Takes the name of a date field and returns an |EngineQuery| for
        documents with dates later than the last_healthy date (if there
        is one) or the start of the monitoring interval (if there isn't).
        """
        start_time = self._get_query_start_time()
        query = QueryFieldset(
            field_name=date_field,
            field_type='DateTimeField',
            operator='gt',
            value=start_time
        )
        return EngineQuery([query])

    @staticmethod
    def _get_sorter(date_field):
        """
        Takes the name of a date field and returns a |Sorter| for
        sorting results in descending order of date.
        """
        sort = SortParam(
            field_name=date_field,
            field_type='DateTimeField',
            order='DESC',
        )
        return Sorter(sort_list=[sort])

    def _get_most_recent_doc(self, distillery):
        """
        Takes a Distillery and the most recent document from the
        monitoring interval, if one exists. Otherwise, returns None.
        """
        date_field = distillery.get_searchable_date_field()
        if date_field:
            query = self._get_query(date_field)
            sorter = self._get_sorter(date_field)
            results = distillery.find(query, sorter, page=1, page_size=1)
            if results['results']:
                return results['results'][0]

    def _update_doc_info(self):
        """
        Looks for the most recently saved doc among the Distilleries
        being monitored, and updates the relevant field in the Monitor.
        """
        for distillery in self.distilleries.all():
            doc = self._get_most_recent_doc(distillery)
            if doc:
                date = distillery.get_date(doc)
                if self.last_healthy is None or date > self.last_healthy:
                    self.last_healthy = date
                    self.last_active_distillery = distillery
                    self.last_saved_doc = doc.get('_id')

    def _set_current_status(self):
        """
        Updates and returns the Monitor's current status.
        """
        is_overdue = self._is_overdue()
        if is_overdue:
            self.status = self._UNHEALTHY
        else:
            self.status = self._HEALTHY
        return self.status

    def _get_title(self):
        """
        Returns a title for an Alert.
        """
        downtime = self._get_inactive_interval()
        return 'Health monitor "%s" has seen no activity for over %s.' \
               % (self.name, downtime)

    def _alert_due(self):
        """
        If the Monitor has previously created an Alert, returns a
        Boolean indicating whether the last time an Alert was generated
        exceeds the Monitor's interval. If the Monitor has never created
        an Alert, returns True. For Monitors with repeating Alerts, this
        is used to determine whether enough time has passed to generate
        another Alert.
        """
        last_alert_time = self._get_last_alert_seconds()
        if last_alert_time:
            return last_alert_time > self._get_interval_in_seconds()
        else:
            return True

    def _create_alert(self):
        """
        Generates an Alert based on the Monitor's alert_level. Returns
        the saved Alert.
        """
        title = self._get_title()
        alert = Alert(
            title=title,
            level=self.alert_level,
            alarm=self,
            distillery=self.last_active_distillery,
            doc_id=self.last_saved_doc
        )
        alert.save()
        return alert

    def _alert(self, old_status):
        """
        Takes a string representing the Monitor's status prior to its
        last update. Determines whether an Alert should be generated,
        and, if so, creates the Alert and saves the Alert's created_date
        to the Monitor's last_alert_date field and the Alert's id to the
        last_alert_id field. Returns None.
        """
        repeat_alert = self.repeating_alerts and self._alert_due()
        status_changed = old_status != self._UNHEALTHY
        if self.alerts_enabled and (repeat_alert or status_changed):
            alert = self._create_alert()
            self.last_alert_date = alert.created_date
            self.last_alert_id = alert.pk
            self.save()

    def _find_last_doc(self):
        """
        Returns the last document saved in the last active Distillery,
        if one exists. Otherwise, returns None.
        """
        return self.last_active_distillery.find_by_id(self.last_saved_doc)

    def _update_fields(self):
        """
        Updates the Monitor's fields relating to its status, and last
        saved document.
        """
        if self.id:
            self._update_doc_info()
        self._set_current_status()

    @property
    def interval(self):
        """
        Returns a string with the Monitor's time_interval and time_unit.
        """
        return str(self.time_interval) + self.time_unit

    def last_doc(self):
        """
        Returns a string of the content for the last document saved to
        one of the Monitor's distilleries. If the Monitor has no record
        of a last saved document, returns None.
        """
        if self.last_active_distillery:
            doc = self._find_last_doc()
            return json.dumps(doc, indent=4)

    last_doc.short_description = _('Last saved document')

    def update_status(self):
        """
        Updates the Monitor's status and creates an Alert if
        appropriate. Returns the Monitor's current status.
        """
        old_status = self.status
        self.save()  # update monitor
        if self.status == self._UNHEALTHY:
            self._alert(old_status)
        return self.status
