# Copyright 2011 iXsystems, Inc.
# All rights reserved
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted providing that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
#####################################################################
from django.forms.utils import flatatt
from django.forms.widgets import Widget
from django.utils.safestring import mark_safe
from dojango.forms.widgets import DojoWidgetMixin

import base64
import json

from freenasUI.middleware.client import client


class CloudSyncWidget(DojoWidgetMixin, Widget):
    dojo_type = 'freeadmin.CloudSync'

    def render(self, name, value, attrs=None):
        from freenasUI.system.models import CloudCredentials
        if value is None:
            value = ''
        with client as c:
            providers = c.call("cloudsync.providers")
        buckets = {provider["name"]: provider["buckets"] for provider in providers}
        bucket_title = {provider["name"]: provider["bucket_title"] for provider in providers}
        task_schemas = {provider["name"]: provider["task_schema"] for provider in providers}
        extra_attrs = {
            'data-dojo-name': name,
            'data-dojo-props': mark_safe("credentials: '{}', initial: '{}'".format(
                base64.b64encode(json.dumps([
                    (str(i), i.id, buckets[i.provider], bucket_title[i.provider], task_schemas[i.provider])
                    for i in CloudCredentials.objects.all()
                ]).encode("ascii")).decode("ascii"),
                base64.b64encode(json.dumps(value).encode("ascii")).decode("ascii"),
            )),
        }
        final_attrs = self.build_attrs(attrs, name=name, **extra_attrs)
        return mark_safe('<div%s></div>' % (flatatt(final_attrs),))
