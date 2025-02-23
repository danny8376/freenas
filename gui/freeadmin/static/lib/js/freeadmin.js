/*-
 * Copyright (c) 2011 iXsystems, Inc.
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 * OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 *
 */

var _webshell;
var Middleware;
var middlewareTokenUrl;

require([
    "dojo",
    "dojo/_base/array",
    "dojo/_base/connect",
    "dojo/_base/event",
    "dojo/_base/fx",
    "dojo/_base/lang",
    "dojo/_base/window",
    "dojo/data/ItemFileReadStore",
    "dojo/dom",
    "dojo/dom-attr",
    "dojo/dom-class",
    "dojo/dom-construct",
    "dojo/dom-style",
    "dojo/fx",
    "dojo/html",
    "dojo/json",
    "dojo/mouse",
    "dojo/on",
    "dojo/parser",
    "dojo/query",
    "dojo/ready",
    "dojo/request/iframe",
    "dojo/request/xhr",
    "dojo/rpc/JsonService",
    "dojo/store/Memory",
    "dojo/NodeList-traverse",
    "dojo/NodeList-manipulate",
    "freeadmin/tree/Tree",
    "freeadmin/CloudSync",
    "freeadmin/ESCDialog",
    "freeadmin/Menu",
    "freeadmin/Middleware",
    "freeadmin/Progress",
    "freeadmin/RRDControl",
    "freeadmin/VolumeManager",
    "freeadmin/WebShell",
    "freeadmin/tree/TreeLazy",
    "freeadmin/tree/JsonRestStore",
    "freeadmin/tree/ForestStoreModel",
    "freeadmin/form/Cron",
    "freeadmin/form/PathSelector",
    "freeadmin/form/UnixPerm",
    "dijit/_base/manager",
    "dijit/form/Button",
    "dijit/form/CheckBox",
    "dijit/form/ComboBox",
    "dijit/form/FilteringSelect",
    "dijit/form/Form",
    "dijit/form/MultiSelect",
    "dijit/form/NumberTextBox",
    "dijit/form/Select",
    "dijit/form/SimpleTextarea",
    "dijit/form/Textarea",
    "dijit/form/TextBox",
    "dijit/form/RadioButton",
    "dijit/form/TimeTextBox",
    "dijit/form/ValidationTextBox",
    "dijit/layout/BorderContainer",
    "dijit/layout/ContentPane",
    "dijit/layout/TabContainer",
    "dijit/registry",
    "dijit/Dialog",
    "dijit/MenuBar",
    "dijit/MenuBarItem",
    "dijit/PopupMenuBarItem",
    "dijit/DropDownMenu",
    "dijit/ProgressBar",
    "dijit/Tooltip",
    "dojox/form/BusyButton",
    "dojox/form/CheckedMultiSelect",
    "dojox/grid/EnhancedGrid",
    "dojox/grid/enhanced/plugins/DnD",
    "dojox/grid/enhanced/plugins/Menu",
    "dojox/grid/enhanced/plugins/NestedSorting",
    "dojox/grid/enhanced/plugins/IndirectSelection",
    "dojox/grid/enhanced/plugins/Pagination",
    "dojox/grid/enhanced/plugins/Filter",
    "dojox/grid/TreeGrid",
    "dojox/uuid/_base",
    "dojox/uuid/generateRandomUuid",
    "dojox/validate"
    ], function(
    dojo,
    dArray,
    dConnect,
    dEvent,
    dFx,
    lang,
    dWindow,
    ItemFileReadStore,
    dom,
    domAttr,
    domClass,
    domConstruct,
    domStyle,
    fx,
    html,
    JSON,
    mouse,
    on,
    parser,
    query,
    ready,
    iframe,
    xhr,
    JsonService,
    Memory,
    NodeListTraverse,
    NodeListManipulate,
    Tree,
    CloudSync,
    ESCDialog,
    fMenu,
    fMiddleware,
    Progress,
    RRDControl,
    VolumeManager,
    WebShell,
    TreeLazy,
    JsonRestStore,
    ForestStoreModel,
    Cron,
    PathSelector,
    UnixPerm,
    manager,
    Button,
    CheckBox,
    ComboBox,
    FilteringSelect,
    Form,
    MultiSelect,
    NumberTextBox,
    Select,
    SimpleTextarea,
    Textarea,
    TextBox,
    RadioButton,
    TimeTextBox,
    ValidationTextBox,
    BorderContainer,
    ContentPane,
    TabContainer,
    registry,
    Dialog,
    MenuBar,
    MenuBarItem,
    PopupMenuBarItem,
    DropDownMenu,
    ProgressBar,
    Tooltip,
    BusyButton,
    CheckedMultiSelect,
    EnhancedGrid,
    enhancedDnD,
    enhancedMenu,
    enhancedNestedSorting,
    enhancedIndirectSelection,
    enhancedPagination,
    enhancedFilter,
    TreeGrid,
    uuidBase,
    generateRandomUuid,
    dojoxvalidate
    ) {

    Menu = new fMenu();
    Middleware = new fMiddleware({tokenUrl: middlewareTokenUrl});

    humanizeSize = function(value, integer) {
      var map = [
        ['PiB', 1125899906842624],
        ['TiB', 1099511627776],
        ['GiB', 1073741824],
        ['MiB', 1048576],
        ['KiB', 1024],
        ['B', 1]
      ];
      for(var i=0;i<map.length;i++) {
        if(value > map[i][1]) {
          if(integer) {
            return new Int(value / map[i][1]) + ' ' + map[i][0];
          } else {
            return (value / map[i][1]).toFixed(2) + ' ' + map[i][0];
          }
        }
      }
      return value + ' B';
    }

    restartHttpd = function(newurl) {

        var handle = function(data) {
            if(newurl) {
                setTimeout(function () {
                    window.location = newurl;
                }, 1500);
            }
        };

        xhr.get('/legacy/system/restart-httpd/', {
            sync: true
        }).then(handle, handle);

    }

    reloadHttpd = function(newurl) {

        var handle = function(data) {
            if(newurl) {
                setTimeout(function () {
                    window.location = newurl;
                }, 1500);
            }
        };

        xhr.get('/legacy/system/reload-httpd/', {
            sync: true
        }).then(handle, handle);

    }

    checkRunning = function(newurl) {

            try {
                setTimeout( function () {
                    window.location = newurl;
                }, 10000);
            }
            catch (e) {
                setTimeout( function () {
                    checkRunning(newurl);
                }, 4000);
            }
    }

    evilrestartHttpd = function(newurl) {

      dialog = new Dialog({
                    title: gettext('Restarting WebGUI'),
                    content: "Please wait while the WebGUI restarts...</br>Refresh your browser manually if unresponsive after 15 seconds",
                    //parseOnLoad: true,
                    closable: true,
                    style: "max-width: 75%;max-height:70%;background-color:white;overflow:auto;",
                    onHide: function() {
                    setTimeout(lang.hitch(this, 'destroyRecursive'), manager.defaultDuration);
                    }
                });
      dialog.show();
      xhr.get('/legacy/system/restart-httpd-all/', {
                sync: true
            }).then(checkRunning(newurl));

    }

    get_selected_plugin = function() {
        var plugin = null;
        var grid = dom.byId("dgrid_available").grid;
        for (var i in grid.selection) {
            plugin = grid.row(i).data;
            break;
        }

        if (!plugin) {
            console.log("Something is wrong here, now plugin found!");
            return null;
        }

        return plugin;
    }

    ask_service = function(srv) {

        dialog = new Dialog({
            title: gettext('Enable service'),
            href: '/legacy/services/enable/'+srv+'/',
            parseOnLoad: true,
            closable: true,
            style: "max-width: 75%;max-height:70%;background-color:white;overflow:auto;",
            onHide: function() {
                setTimeout(lang.hitch(this, 'destroyRecursive'), manager.defaultDuration);
            }
        });
        dialog.show();

    }

    add_formset = function(a, url, name) {
        xhr.get(url, {
            query: {
                fsname: name
            },
            sync: true
            }).then(function(data) {
                var extra = registry.byId("id_"+name+"-TOTAL_FORMS");
                var extran = extra.get("value");
                data = data.replace(/__prefix__/g, extran);
                var div = domConstruct.create("table");
                query(a.parentNode.parentNode).before(div);
                div.innerHTML = data;
                parser.parse(div);
                extra.set('value', parseInt(extran) + 1);

            });

    }

    alertDismiss = function() {
        var input = this;
        var msgid = input.value;
        var dismiss;
        if(input.checked) {
            dismiss = false;
        } else {
            dismiss = true;
        }
        xhr.put("/api/v1.0/system/alert/dismiss/", {
            headers: {"X-CSRFToken": CSRFToken, "Content-Type": "application/json"},
            data: JSON.stringify({"id": msgid, "dismiss": dismiss})
        }).then(function(data) {
            loadalert();
        });
    }

    remoteCipherConfirm = function(value) {
      cipher = this;
      if(value == 'disabled') {
        var dialog = new Dialog({
          title: gettext("Warning!"),
          id: "editconfirmscary_dialog",
          content: domConstruct.create("p", {innerHTML: gettext("This option DISABLES ENCRYPTION for replication tasks and should ONLY be used on a secure LAN!") + "<br />" + gettext("Are you sure you want to do this?")}, null)
        });
        dialog.okButton = new Button({label: "Yes"});
        dialog.cancelButton = new Button({label: "No"});
        dialog.addChild(dialog.okButton);
        dialog.addChild(dialog.cancelButton);
        dialog.okButton.on('click', function(e){
          cipher.set('oldvalue', value);
          dialog.destroy();
        });
        dialog.cancelButton.on('click', function(e){
          cipher.set('value', cipher.get('oldvalue'), false);
          dialog.destroy();
        });
        dialog.startup();
        dialog.show();
      } else {
        this.set('oldvalue', value);
      }
    }

    var canceled = false;

    setVisible = function(id, visible) {
        var widget = registry.byId(id);
        if (widget)
        {
            domStyle.set(widget.domNode.parentNode.parentNode, "display", visible ? "" : "none");
            widget.set("disabled", !visible);
        }
        else
        {
            var widget = registry.byId(id + "_0");
            if (widget)
            {
                domStyle.set(widget.domNode.parentNode.parentNode.parentNode.parentNode.parentNode,
                             "display", visible ? "" : "none");
            }
            else
            {
                throw new Error("Widget " + id + " not found")
            }
        }
    }

    hideGeneric = function(checkboxid, farray, inverted) {

        if(inverted == undefined) inverted = false;

        var box = registry.byId(checkboxid);
        if(inverted == true) {
            toset = !box.get("value");
        } else{
            toset = box.get("value");
        }
        for(var i=0;i<farray.length;i++) {
            setVisible(farray[i], toset);
        }

    }

    toggleGeneric = function(checkboxid, farray, inverted) {

        if(inverted == undefined) inverted = false;

        var box = registry.byId(checkboxid);
        if(inverted == true) {
            toset = !box.get("value");
        } else{
            toset = box.get("value");
        }
        for(var i=0;i<farray.length;i++) {
            var widget = registry.byId(farray[i]);
            if(widget)
                widget.set('disabled', toset);
        }

    }

    disableGeneric = function(domid, farray, checkfn) {

        var box = registry.byId(domid);
        var bool = checkfn(box);
        for(var i=0;i<farray.length;i++) {
            if(bool) {
                domClass.add(registry.byId(farray[i]).domNode, ['dijitDisabled', 'dijitTextBoxDisabled', 'dijitValidationTextBoxDisabled']);
                registry.byId(farray[i]).set('readOnly', true);
            } else {
                domClass.remove(registry.byId(farray[i]).domNode, ['dijitDisabled', 'dijitTextBoxDisabled', 'dijitValidationTextBoxDisabled']);
                registry.byId(farray[i]).set('readOnly', false);
            }
        }

    }

    toggleNFSService = function() {

      var v4 = registry.byId("id_nfs_srv_v4");
      var v4v3owner = registry.byId("id_nfs_srv_v4_v3owner");
      var srv16 = registry.byId("id_nfs_srv_16");

      if(srv16.get("value") == 'on') {
        v4v3owner.set("value", false);
      }
      if(v4v3owner.get("value") == 'on') {
        srv16.set("value", false);
      }

      if(v4.get("value") == 'on') {

        if(v4v3owner.get("value") == 'on') {
          srv16.set('disabled', true);
        } else {
          srv16.set('disabled', false);
        }
        if(srv16.get("value") == 'on') {
          v4v3owner.set('disabled', true);
        } else {
          v4v3owner.set('disabled', false);
        }

      } else {

        v4v3owner.set("value", false);
        v4v3owner.set("disabled", true);
        srv16.set("disabled", false);

      }

    }

    targetMode = function() {

      var q = query('input[name=iscsi_target_mode]:checked');
      if(q.length == 0) return false;
      var mode = q[0];
      var tf = registry.byId('id_targetgroups_set-TOTAL_FORMS')
      var oldvalue = tf.get('oldvalue');
      tf.set('oldvalue', tf.get('value'));

      if(mode.value == 'fc') {
        domStyle.set('inline_iscsitarget_formset_iscsitargetgroups', 'display', 'none');
        tf.set('value', '0');
      } else {
        domStyle.set('inline_iscsitarget_formset_iscsitargetgroups', 'display', '');
        if(oldvalue) tf.set('value', oldvalue);
      }

    }

    generic_certificate_autopopulate = function(url) {
        var certinfo = null;
        xhr.get(url, {
            sync: true
        }).then(function(data) {
            certinfo = JSON.parse(data);
            if (!certinfo) {
                return;
            }
        });

        cert_country = registry.byId("id_cert_country");
        if (certinfo.cert_country) {
            cert_country.set("value", certinfo.cert_country);
        }

        cert_state = registry.byId("id_cert_state");
        if (certinfo.cert_state) {
            cert_state.set("value", certinfo.cert_state);
        }

        cert_city = registry.byId("id_cert_city");
        if (certinfo.cert_city) {
            cert_city.set("value", certinfo.cert_city);
        }

        cert_organization = registry.byId("id_cert_organization");
        if (certinfo.cert_organization) {
            cert_organization.set("value", certinfo.cert_organization);
        }

        cert_organizational_unit = registry.byId("id_cert_organizational_unit");
        if (certinfo.cert_organizational_unit) {
            cert_organizational_unit.set("value", certinfo.cert_organizational_unit);
        }

        cert_email = registry.byId("id_cert_email");
        if (certinfo.cert_email) {
            cert_email.set("value", certinfo.cert_email);
        }
    }

    Cert_EC_key = function() {
        var key_type = registry.byId("id_cert_key_type").get("value");
        cert_ec_curve = registry.byId("id_cert_ec_curve");
        cert_key_length =registry.byId("id_cert_key_length");
        if (key_type == "EC"){
            cert_key_length.set("disabled", true);
            cert_ec_curve.set("disabled", false);
        } else {
          cert_key_length.set("disabled", false);
          cert_ec_curve.set("disabled", true);
        }
    }

    CA_autopopulate = function() {
        var signedby_id = registry.byId("id_cert_signedby").get("value");
        generic_certificate_autopopulate(
            '/legacy/system/CA/info/' + signedby_id + '/'
        );
    }

    get_directoryservice_set = function(enable) {
        var fullset = [
            "ad_enable",
            "dc_enable",
            "ldap_enable",
            "nis_enable"
        ];

        set = []
        for (var index in fullset) {
            if (fullset[index] != enable) {
                set.push(fullset[index]);
            }
        }

        return set;
    }

    directoryservice_mutex_toggle = function(ds_enable, ds_obj) {
        xhr.get('/legacy/directoryservice/status/', {
            sync: true 
        }). then(function(data) {
            s = JSON.parse(data);
            set = get_directoryservice_set(ds_enable);
            for (index in set) {
                key = set[index]; 
                if (s[key] == true) {
                    ds_obj.set("disabled", true);
                    break;
                }
            }
        });
    }

    activedirectory_mutex_toggle = function() {
        ad = registry.byId("id_ad_enable");
        directoryservice_mutex_toggle('ad_enable', ad);
    }

    activedirectory_idmap_check = function() {
        idmap = registry.byId("id_ad_idmap_backend");

        ad_idmap = idmap.get("value");
        if (ad_idmap != "rid") {
            var dialog = new Dialog({
                title: gettext("Active Directory IDMAP change!"),
                id: "AD_idmap_scary_dialog",
                content: domConstruct.create(
                    "p", {
                        innerHTML: gettext( 
                            gettext("<font color='red'>STOP</font>: Do you know what you are doing?<br /><br />") +
                            gettext("The idmap_ad plugin provides a way for Winbind to read id mappings from<br />") +
                            gettext("an AD server that uses RFC2307/SFU schema extensions. This module<br />") +
                            gettext("implements only the \"idmap\" API, and is READONLY. Mappings must be<br />") +
                            gettext("provided in advance by the administrator by adding the uidNumber<br />") +
                            gettext("attributes for users and gidNumber attributes for groups in the AD.<br />") +
                            gettext("Winbind will only map users that have a uidNumber and whose primary<br />") +
                            gettext("group have a gidNumber attribute set. It is however recommended that<br />") +
                            gettext("all groups in use have gidNumber attributes assigned, otherwise they<br />") +
                            gettext("are not working.<br /><br />") +
                            gettext("<font color='red'>STOP</font>: If your Active Directory is not configured for this, it will not work.<br><br>")
                        )
                    },
                    null
                )
            })

            dialog.okButton = new Button({label: "Yes"});
            dialog.cancelButton = new Button({label: "No"});
            dialog.addChild(dialog.okButton);
            dialog.addChild(dialog.cancelButton);
            dialog.okButton.on('click', function(e){
                dialog.destroy();
            });
            dialog.cancelButton.on('click', function(e){
                idmap.set('value', 'rid', false);
                dialog.destroy();
            });
            dialog.startup();
            dialog.show();
        }
    }

    ldap_mutex_toggle = function() {
        ldap = registry.byId("id_ldap_enable");
        directoryservice_mutex_toggle('ldap_enable', ldap);
    }

    nis_mutex_toggle = function() {
        nis = registry.byId("id_nis_enable");
        directoryservice_mutex_toggle('nis_enable', nis);
    }

    support_production_init = function() {
        domStyle.set(registry.byId("id_send_debug").domNode.parentNode.parentNode, 'display', 'none');
    }

    support_production_toggle = function() {
        if (registry.byId("id_production").get("value")) {
            domStyle.set(registry.byId("id_send_debug").domNode.parentNode.parentNode, 'display', 'table-row');
            registry.byId("id_send_debug").set("checked", true);
        } else {
            domStyle.set(registry.byId("id_send_debug").domNode.parentNode.parentNode, 'display', 'none');
            registry.byId("id_send_debug").set("checked", false);
        }
    }

    directoryservice_idmap_onclick = function(eid, ds_type, ds_id) {
        var widget = registry.byId(eid);
        var idmap_type = widget.get("value");
        var idmap_url = "/legacy/directoryservice/idmap_backend/" +
            ds_type + "/" + ds_id + "/" + idmap_type + "/";
        var idmap_name = null;
        var id = -1;

        //console.log("Idmap URL:", idmap_url);

        xhr.get(idmap_url, {
            sync: true,
            handleAs: 'json'
        }).then(function(data) {
                id = data.idmap_id;
                idmap_type = data.idmap_type;
                idmap_name = data.idmap_name;
            }
        );

        if (id > 0) {
            var edit_url = "/legacy/directoryservice/idmap_" + idmap_name + "/" + id + "/";

            //console.log("Edit URL:", edit_url, "ID:", id);

            editObject("Edit Idmap", edit_url, [this,]);
        }
    }

    directoryservice_idmap_onload = function(eid, ds_type, ds_id) {
        var table = query("#" + eid)[0];
        var td = table.parentNode;

        var editbtn = new Button({
            label: gettext("Edit"),
            style: "float: right; margin-left: 20px",
            onClick: function() {
                directoryservice_idmap_onclick(eid, ds_type, ds_id);
            }
        });
        editbtn.placeAt(td);
    }

    mpAclChange = function(acl) {
      var mode_en = registry.byId("id_mp_mode_en");
      var mode = registry.byId("id_mp_mode");
      if(acl.get('value') === false) {
        // do nothing
      } else if(acl.get('value') == 'remove') {
        mode_en.set('disabled', false);
        mode.set('disabled', false);
      } else {
        mode_en.set('disabled', true);
        mode.set('disabled', true);
      }
    }

    genericSelectFields = function(selectid, map) {
      /*
       * Show and hide fields base on the value of a single widget.
       */

      var map = JSON.parse(map);
      var select = registry.byId(selectid);
      var value = select.get('value');
      for(var key in map) {
        var display;
        if(key == value) {
          display = 'table-row';
        } else {
          display = 'none';
        }

        for(var i in map[key]) {
          var field = registry.byId(map[key][i]);
          domStyle.set(field.domNode.parentNode.parentNode, 'display', display);
        }
      }

    }

    rsyncModeToggle = function() {

        var select = registry.byId("id_rsync_mode");
        var modname = registry.byId("id_rsync_remotemodule");
        var rpathval = registry.byId("id_rsync_validate_rpath");
        var path = registry.byId("id_rsync_remotepath");
        var port = registry.byId("id_rsync_remoteport");
        var trm = modname.domNode.parentNode.parentNode;
        var trp = path.domNode.parentNode.parentNode;
        var trpo = port.domNode.parentNode.parentNode;
        var trpa = rpathval.domNode.parentNode.parentNode;
        if(select.get('value') == 'ssh') {
            domStyle.set(trm, "display", "none");
            domStyle.set(trp, "display", "table-row");
            domStyle.set(trpo, "display", "table-row");
            domStyle.set(trpa, "display", "table-row");
        } else {
            domStyle.set(trm, "display", "table-row");
            domStyle.set(trp, "display", "none");
            domStyle.set(trpo, "display", "none");
            domStyle.set(trpa, "display", "none");
        }

    }

    sshCredentialsTypeToggle = function() {
        if (registry.byId("id_type").get("value") == "MANUAL")
        {
            setVisible("id_host", true);
            setVisible("id_port", true);
            setVisible("id_remote_host_key", true);
            domStyle.set(registry.byId("btn_Fake").domNode, "display", "");

            setVisible("id_url", false);
            setVisible("id_token", false);
        }
        else
        {
            setVisible("id_host", false);
            setVisible("id_port", false);
            setVisible("id_remote_host_key", false);
            domStyle.set(registry.byId("btn_Fake").domNode, "display", "none");

            setVisible("id_url", true);
            setVisible("id_token", true);
        }
    }

    replicationSetup = function() {
        var tr;

        tr = registry.byId("id_repl_enable_schedule").domNode.parentNode.parentNode;
        for (var i = 0; i < 8; i++)
        {
            domStyle.set(tr, "background", "#ebffd7");
            domStyle.set(tr.children[0], "background", "#ebffd7");
            tr = tr.nextSibling;
        }

        tr = registry.byId("id_repl_enable_restrict_schedule").domNode.parentNode.parentNode;
        for (var i = 0; i < 8; i++)
        {
            domStyle.set(tr, "background", "#ebd7ff");
            domStyle.set(tr.children[0], "background", "#ebd7ff");
            tr = tr.nextSibling;
        }
    }

    replicationToggle = function() {
        if (registry.byId("id_repl_direction").get("value") == "PUSH")
        {
            document.querySelector('label[for="id_repl_naming_schema"]').innerHTML = "Also include naming schema";
        }
        else
        {
            document.querySelector('label[for="id_repl_naming_schema"]').innerHTML = "Naming schema";
        }

        var schedule = ["schedule_minute", "schedule_hour", "schedule_month", "schedule_daymonth", "schedule_dayweek",
                        "schedule_begin", "schedule_end"];
        var restrictSchedule = ["restrict_schedule_minute", "restrict_schedule_hour", "restrict_schedule_month",
                                "restrict_schedule_daymonth", "restrict_schedule_dayweek", "restrict_schedule_begin",
                                "restrict_schedule_end"];
        var policies = {
            "SOURCE": [],
            "CUSTOM": ["lifetime_value", "lifetime_unit"],
            "NONE": [],
        };
        var newFeatures = (
            ["exclude", "periodic_snapshot_tasks", "naming_schema", "auto", "only_matching_schedule",
             "allow_from_scratch", "hold_pending_snapshots", "retention_policy", "dedup", "large_block", "embed",
             "compressed", "retries"].
            concat("enable_schedule").concat(schedule).
            concat("enable_restrict_schedule").concat(restrictSchedule).
            concat(policies["SOURCE"]).concat(policies["CUSTOM"]).concat(policies["NONE"])
        );
        var transports = {
            "SSH": newFeatures.concat(["ssh_credentials", "compression", "speed_limit"]),
            "SSH+NETCAT": newFeatures.concat(["ssh_credentials", "netcat_active_side",
                                              "netcat_active_side_listen_address", "netcat_active_side_port_min",
                                              "netcat_active_side_port_max", "netcat_passive_side_connect_address"]),
            "LOCAL": newFeatures,
            "LEGACY": ["ssh_credentials"],
        };

        var visible = {};

        // Hide all
        for (var k in transports)
        {
            var fields = transports[k];
            for (var i in fields)
            {
                var name = fields[i];
                visible[name] = false;
            }
        }

        // Show available for current transport
        var fields = transports[registry.byId("id_repl_transport").get("value")];
        for (var i in fields)
        {
            var name = fields[i];
            visible[name] = true;
        }

        // Pull
        if (registry.byId("id_repl_direction").get("value") == "PULL")
        {
            visible["periodic_snapshot_tasks"] = false;
        }

        // Recursive

        if (!registry.byId("id_repl_recursive").get("value"))
        {
            visible["exclude"] = false;
        }

        // Auto

        var scheduleVisible = false;
        var direction = registry.byId("id_repl_direction").get("value");
        if (direction == "PUSH")
        {
            scheduleVisible = (
                registry.byId("id_repl_auto").get("value") &&
                registry.byId("id_repl_periodic_snapshot_tasks").get("value").length == 0
            );
        }
        if (direction == "PULL")
        {
            scheduleVisible = (
                registry.byId("id_repl_auto").get("value")
            );
        }
        if (!scheduleVisible)
        {
            visible["enable_schedule"] = false;
        }
        if (!visible["enable_schedule"] || !registry.byId("id_repl_enable_schedule").get("value"))
        {
            for (var i in schedule)
            {
                var name = schedule[i];
                visible[name] = false;
            }
            visible["only_matching_schedule"] = false;
        }
        if (!visible["enable_restrict_schedule"] || !registry.byId("id_repl_enable_restrict_schedule").get("value"))
        {
            for (var i in restrictSchedule)
            {
                var name = restrictSchedule[i];
                visible[name] = false;
            }
        }

        // Retention policy

        if (registry.byId("id_repl_retention_policy").get("value") != "CUSTOM")
        {
            for (var i in policies["CUSTOM"])
            {
                var name = policies["CUSTOM"][i];
                visible[name] = false;
            }
        }

        // Run

        for (var name in visible)
        {
            setVisible("id_repl_" + name, visible[name]);
        }
    }

    deviceTypeToggle = function() {

        var dtype = registry.byId("id_dtype");
        var cdrom_path = registry.byId("id_CDROM_path").domNode.parentNode.parentNode;
        var disk_mode = registry.byId("id_DISK_mode").domNode.parentNode.parentNode;
        var disk_zvol = registry.byId("id_DISK_zvol").domNode.parentNode.parentNode;
        var disk_raw = registry.byId("id_DISK_raw").domNode.parentNode.parentNode;
        var disk_sectorsize = registry.byId("id_DISK_sectorsize").domNode.parentNode.parentNode;
        var nic_type = registry.byId("id_NIC_type").domNode.parentNode.parentNode;
        var nic_mac = registry.byId("id_NIC_mac").domNode.parentNode.parentNode;
        var nic_attach = registry.byId("id_NIC_attach").domNode.parentNode.parentNode;
        var vnc_wait = registry.byId("id_VNC_wait").domNode.parentNode.parentNode;
        var vnc_port = registry.byId("id_VNC_port").domNode.parentNode.parentNode;
        var vnc_resolution = registry.byId("id_VNC_resolution").domNode.parentNode.parentNode;
        var vnc_bind = registry.byId("id_VNC_bind").domNode.parentNode.parentNode;
        var vnc_password = registry.byId("id_VNC_password").domNode.parentNode.parentNode;
        var vnc_web = registry.byId("id_VNC_web").domNode.parentNode.parentNode;

        domStyle.set(cdrom_path, "display", "none");
        domStyle.set(disk_mode, "display", "none");
        domStyle.set(disk_zvol, "display", "none");
        domStyle.set(disk_raw, "display", "none");
        domStyle.set(disk_sectorsize, "display", "none");
        domStyle.set(nic_type, "display", "none");
        domStyle.set(nic_mac, "display", "none");
        domStyle.set(nic_attach, "display", "none");
        domStyle.set(vnc_wait, "display", "none");
        domStyle.set(vnc_port, "display", "none");
        domStyle.set(vnc_resolution, "display", "none");
        domStyle.set(vnc_bind, "display", "none");
        domStyle.set(vnc_password, "display", "none");
        domStyle.set(vnc_web, "display", "none");

        if(dtype.get('value') == 'DISK') {
          domStyle.set(disk_mode, "display", "");
          domStyle.set(disk_zvol, "display", "");
          domStyle.set(disk_sectorsize, "display", "");
        } else if(dtype.get('value') == 'RAW') {
          domStyle.set(disk_raw, "display", "");
          domStyle.set(disk_mode, "display", "");
          domStyle.set(disk_sectorsize, "display", "");
        } else if(dtype.get('value') == 'CDROM') {
          domStyle.set(cdrom_path, "display", "");
        } else if(dtype.get('value') == 'NIC') {
          domStyle.set(nic_type, "display", "");
          domStyle.set(nic_mac, "display", "");
          domStyle.set(nic_attach, "display", "");
        } else if(dtype.get('value') == 'VNC') {
          domStyle.set(vnc_resolution, "display", "");
          domStyle.set(vnc_port, "display", "");
          domStyle.set(vnc_wait, "display", "");
          domStyle.set(vnc_bind, "display", "");
          domStyle.set(vnc_password, "display", "");
          domStyle.set(vnc_web, "display", "");
        }

    }

    alertServiceTypeToggle = function() {

        var type = registry.byId("id_type");

        // Common fields between all API
        var cluster_name = registry.byId("id_cluster_name").domNode.parentNode.parentNode;
        var username = registry.byId("id_username").domNode.parentNode.parentNode;
        var password = registry.byId("id_password").domNode.parentNode.parentNode;
        var enabled = registry.byId("id_enabled").domNode.parentNode.parentNode;
        var _url = registry.byId("id_url").domNode.parentNode.parentNode;

        // Influxdb
        var host = registry.byId("id_host").domNode.parentNode.parentNode;
        var database = registry.byId("id_database").domNode.parentNode.parentNode;
        var series_name = registry.byId("id_series_name").domNode.parentNode.parentNode;

        // Slack
        var channel = registry.byId("id_channel").domNode.parentNode.parentNode;
        var icon_url = registry.byId("id_icon_url").domNode.parentNode.parentNode;
        var detailed = registry.byId("id_detailed").domNode.parentNode.parentNode;

        // Mattermost
        var team = registry.byId("id_team").domNode.parentNode.parentNode;

        // PagerDuty
        var service_key = registry.byId("id_service_key").domNode.parentNode.parentNode;
        var client_name = registry.byId("id_client_name").domNode.parentNode.parentNode;

        // HipChat
        var hfrom = registry.byId("id_hfrom").domNode.parentNode.parentNode;
        var base_url = registry.byId("id_base_url").domNode.parentNode.parentNode;
        var room_id = registry.byId("id_room_id").domNode.parentNode.parentNode;
        var auth_token = registry.byId("id_auth_token").domNode.parentNode.parentNode;

        // OpsGenie
        var api_key = registry.byId("id_api_key").domNode.parentNode.parentNode;
        var api_url = registry.byId("id_api_url").domNode.parentNode.parentNode;

        // AWS SNS
        var region = registry.byId("id_region").domNode.parentNode.parentNode;
        var topic_arn = registry.byId("id_topic_arn").domNode.parentNode.parentNode;
        var aws_access_key_id = registry.byId("id_aws_access_key_id").domNode.parentNode.parentNode;
        var aws_secret_access_key = registry.byId("id_aws_secret_access_key").domNode.parentNode.parentNode;

        // VictorOps
        var routing_key = registry.byId("id_routing_key").domNode.parentNode.parentNode;

        // Mail
        var email = registry.byId("id_email").domNode.parentNode.parentNode;

        domStyle.set(_url, "display", "none");
        domStyle.set(cluster_name, "display", "none");
        domStyle.set(username, "display", "none");
        domStyle.set(password, "display", "none");
        domStyle.set(host, "display", "none");
        domStyle.set(database, "display", "none");
        domStyle.set(series_name, "display", "none");
        domStyle.set(channel, "display", "none");
        domStyle.set(icon_url, "display", "none");
        domStyle.set(detailed, "display", "none");
        domStyle.set(team, "display", "none");
        domStyle.set(service_key, "display", "none");
        domStyle.set(client_name, "display", "none");
        domStyle.set(hfrom, "display", "none");
        domStyle.set(base_url, "display", "none");
        domStyle.set(room_id, "display", "none");
        domStyle.set(auth_token, "display", "none");
        domStyle.set(api_key, "display", "none");
        domStyle.set(api_url, "display", "none");
        domStyle.set(region, "display", "none");
        domStyle.set(topic_arn, "display", "none");
        domStyle.set(aws_access_key_id, "display", "none");
        domStyle.set(aws_secret_access_key, "display", "none");
        domStyle.set(routing_key, "display", "none");
        domStyle.set(email, "display", "none");

        if(type.get('value') == 'InfluxDB') {
            domStyle.set(host, "display", "table-row");
            domStyle.set(username, "display", "table-row");
            domStyle.set(password, "display", "table-row");
            domStyle.set(database, "display", "table-row");
            domStyle.set(series_name, "display", "table-row");
        } else if(type.get('value') == 'Slack') {
            domStyle.set(cluster_name, "display", "table-row");
            domStyle.set(_url, "display", "table-row");
            domStyle.set(channel, "display", "table-row");
            domStyle.set(username, "display", "table-row");
            domStyle.set(icon_url, "display", "table-row");
            domStyle.set(detailed, "display", "table-row");
        } else if(type.get('value') == 'Mattermost') {
            domStyle.set(cluster_name, "display", "table-row");
            domStyle.set(_url, "display", "table-row");
            domStyle.set(username, "display", "table-row");
            domStyle.set(password, "display", "table-row");
            domStyle.set(team, "display", "table-row");
            domStyle.set(channel, "display", "table-row");
        } else if(type.get('value') == 'PagerDuty') {
            domStyle.set(service_key, "display", "table-row");
            domStyle.set(client_name, "display", "table-row");
        } else if(type.get('value') == 'HipChat') {
            domStyle.set(hfrom, "display", "table-row");
            domStyle.set(cluster_name, "display", "table-row");
            domStyle.set(base_url, "display", "table-row");
            domStyle.set(room_id, "display", "table-row");
            domStyle.set(auth_token, "display", "table-row");
        } else if(type.get('value') == 'OpsGenie') {
            domStyle.set(cluster_name, "display", "table-row");
            domStyle.set(api_key, "display", "table-row");
            domStyle.set(api_url, "display", "table-row");
        } else if(type.get('value') == 'AWSSNS') {
            domStyle.set(region, "display", "table-row");
            domStyle.set(topic_arn, "display", "table-row");
            domStyle.set(aws_access_key_id, "display", "table-row");
            domStyle.set(aws_secret_access_key, "display", "table-row");
        } else if(type.get('value') == 'VictorOps') {
            domStyle.set(api_key, "display", "table-row");
            domStyle.set(routing_key, "display", "table-row");
        } else if(type.get('value') == 'Mail') {
            domStyle.set(email, "display", "table-row");
        }
    }

    systemDatasetMigration = function() {
        sys_dataset_pool = registry.byId('id_sys_pool')
        if (!sys_dataset_pool._isReset) {
            var dialog = new Dialog({
                title: gettext("Warning!"),
                id: "Warning_box_dialog",
                content: domConstruct.create(
                    "p", {
                        innerHTML: gettext("The action will result in migration of dataset.") + "<br />" + gettext("Some services will be restarted.") + "<br />" + gettext("NOTE: This is just a warning, to perform the operation you must click Save.")
                    }
                ),
                onHide: function () {
                    if (!this.confirmed) {
                        sys_dataset_pool._isReset = true;
                        sys_dataset_pool.reset();
                    }
                    this.destroy();
                }
            });
            dialog.okButton = new Button({label: gettext("Continue")});
            dialog.cancelButton = new Button({label: gettext("Cancel")});
            dialog.addChild(dialog.okButton);
            dialog.addChild(dialog.cancelButton);
            dialog.okButton.on('click', function(e){
                dialog.confirmed = true;
                dialog.hide();
            });
            dialog.cancelButton.on('click', function(e) {
                dialog.hide();
            });
            dialog.confirmed = false;
            dialog.startup();
            dialog.show();
        } else {
            sys_dataset_pool._isReset = false;
        }

    }


    credentialsProvider = function(provider_id, class_name) {

        var provider = registry.byId(provider_id).get('value');
        var credentialsSchemas = JSON.parse(registry.byId("id_credentials_schemas").get('value'));
        var credentialsOauths = JSON.parse(registry.byId("id_credentials_oauths").get('value'));

        var attributesInput = dom.byId("id_attributes");
        var attributes = JSON.parse(attributesInput.value) || {};

        var updateAttributes = function() {
            var attributes = {};
            for (var i = 0; i < credentialsSchemas[provider].length; i++)
            {
                var property = credentialsSchemas[provider][credentialsSchemas[provider].length - 1 - i];

                var id = "id_attributes_" + property.property;
                if (property.schema.type.indexOf("boolean") != -1) {
                    attributes[property.property] = document.getElementById(id).checked;
                } else if (property.schema.type.indexOf("integer") != -1) {
                    attributes[property.property] = parseInt(document.getElementById(id).value);
                    if (isNaN(attributes[property.property]))
                    {
                        delete attributes[property.property];
                    }
                } else {
                    attributes[property.property] = document.getElementById(id).value;
                }
            }

            attributesInput.value = JSON.stringify(attributes);
        };

        while (true)
        {
            var old = document.getElementsByClassName(class_name);
            if (!old.length)
            {
                break;
            }
            old[0].parentNode.removeChild(old[0]);
        }

        for (var i = 0; i < credentialsSchemas[provider].length; i++)
        {
            var property = credentialsSchemas[provider][credentialsSchemas[provider].length - 1 - i];

            var id = "id_attributes_" + property.property;
            var input;
            if (property.schema.type.indexOf("boolean") != -1)
            {
                input = '<input type="checkbox" id="' + id + '" value="1">';
            }
            else
            {
                input = "<input type='text' id='" + id + "'>";
                if (property.schema.enum)
                {
                    input = "<select id='" + id + "'>";
                    for (var j = 0; j < property.schema.enum.length; j++)
                    {
                        if (typeof property.schema.enum[j] == 'string')
                        {
                            input += '<option>' + property.schema.enum[j] + '</option>';
                        }
                        else
                        {
                            input += '<option value="' + property.schema.enum[j][0] + '">' + property.schema.enum[j][1] + '</option>';
                        }
                    }
                    input += '</select>';
                }
            }
            if (property.property == "service_account_credentials")
            {
                input += '<br /><input type="file" id="service_account_credentials_file" />';
            }

            var newNode = document.createElement("tr");
            newNode.className = "cloud-credentials-attribute";
            newNode.innerHTML = "<th>" + property.schema.title + "</th><td>" + input + "</td>";

            attributesInput.parentNode.insertBefore(newNode, attributesInput.nextSibling);

            if (attributes[property.property])
            {
                if (property.schema.type.indexOf("boolean") != -1)
                {
                    document.getElementById(id).checked = attributes[property.property];
                }
                else
                {
                    document.getElementById(id).value = attributes[property.property].toString();
                }
            }

            if (property.property == "service_account_credentials")
            {
                document.getElementById("service_account_credentials_file").addEventListener('change', (function() {
                    return function(evt) {
                        var files = evt.target.files;
                        f = files[0];
                        var reader = new FileReader();
                        reader.onload = (function(theFile) {
                            return function(e) {
                                document.getElementById("id_attributes_service_account_credentials").value = e.target.result;
                                updateAttributes();
                            };
                        })(f);
                       reader.readAsText(f);
                    }
                })(), false);
            }

            document.getElementById(id).onchange = updateAttributes;
        }
        if (credentialsOauths[provider])
        {
            var newNode = document.createElement("tr");
            newNode.className = "cloud-credentials-attribute";
            newNode.innerHTML = "<th>&nbsp;</th><td><a href='#' onclick='cloudSyncAutomaticConfig(" + JSON.stringify(credentialsOauths[provider]) + "); return false;'>Automatic config</a></td>";

            attributesInput.parentNode.insertBefore(newNode, attributesInput.nextSibling);
        }

        updateAttributes();
    }

    cloudSyncAutomaticConfig = function(url) {
        window.open(url + "?origin=" + encodeURIComponent(window.location.toString()), "_blank", "width=640,height=480");
    }

    window.addEventListener("message", function(message) {
        if (message.data.oauth_portal)
        {
            if (message.data.error)
            {
                alert(message.data.error);
            }
            else
            {
                for (k in message.data.result)
                {
                    if (document.getElementById("id_attributes_" + k))
                    {
                        document.getElementById("id_attributes_" + k).value = message.data.result[k];
                        document.getElementById("id_attributes_" + k).onchange();
                    }
                }
            }
        }
    }, false);

    cloudSyncDirectionToggle = function() {

        var direction = registry.byId("id_direction");
        var snapshot = registry.byId("id_snapshot");
        var tr = snapshot.domNode.parentNode.parentNode;
        if(direction.get('value') == 'PUSH') {
            domStyle.set(tr, "display", "table-row");
        } else {
            snapshot.set("value", false);
            domStyle.set(tr, "display", "none");
        }

    }

    cloudSyncEncryptionToggle = function() {

        var checkbox = registry.byId("id_encryption");

        var filename_encryption = registry.byId("id_filename_encryption");
        var tr_filename_encryption = filename_encryption.domNode.parentNode.parentNode;
        var encryption_password = registry.byId("id_encryption_password");
        var tr_encryption_password = encryption_password.domNode.parentNode.parentNode;
        var encryption_salt = registry.byId("id_encryption_salt");
        var tr_encryption_salt = encryption_salt.domNode.parentNode.parentNode;

        if (checkbox.checked) {
            domStyle.set(tr_filename_encryption, "display", "");
            domStyle.set(tr_encryption_password, "display", "");
            domStyle.set(tr_encryption_salt, "display", "");
        } else {
            domStyle.set(tr_filename_encryption, "display", "none");
            domStyle.set(tr_encryption_password, "display", "none");
            domStyle.set(tr_encryption_salt, "display", "none");
        }

    }

    ddnsCustomProviderToggle = function() {
        var dropdown = document.querySelector("input[name=ddns_provider]");
        var custom_ddns_server = registry.byId("id_ddns_custom_ddns_server");
        var tr_custom_ddns_server = custom_ddns_server.domNode.parentNode.parentNode;
        var custom_ddns_path = registry.byId("id_ddns_custom_ddns_path");
        var tr_custom_ddns_path = custom_ddns_path.domNode.parentNode.parentNode;
        if (dropdown.value == "custom") {
            domStyle.set(tr_custom_ddns_server, "display", "");
            domStyle.set(tr_custom_ddns_path, "display", "");
        } else {
            domStyle.set(tr_custom_ddns_server, "display", "none");
            domStyle.set(tr_custom_ddns_path, "display", "none");
        }
    }

    extentZvolToggle = function() {

        var select = registry.byId("id_iscsi_target_extent_disk");
        var type = registry.byId("id_iscsi_target_extent_type");
        var threshold = registry.byId("id_iscsi_target_extent_avail_threshold");
        var trt = threshold.domNode.parentNode.parentNode;

        if(type.get('value') == 'Disk' && select.get('value').indexOf('zvol/') == 0) {
            domStyle.set(trt, "display", "table-row");
        } else if(type.get('value') == 'File') {
            domStyle.set(trt, "display", "table-row");
        } else {
            domStyle.set(trt, "display", "none");
        }

    }

    webdavprotocolToggle = function() {

        var select = registry.byId("id_webdav_protocol");
        var portssl = registry.byId("id_webdav_tcpportssl");
        var trpossl = portssl.domNode.parentNode.parentNode;
        var port = registry.byId("id_webdav_tcpport");
        var trpo = port.domNode.parentNode.parentNode;
        var cert = registry.byId("id_webdav_certssl");
        var trpocert = cert.domNode.parentNode.parentNode;
        if (select.get('value') == 'http') {
            domStyle.set(trpo,"display","");
            domStyle.set(trpossl,"display","none");
            domStyle.set(trpocert,"display","none");
        } else if (select.get('value') == 'https') {
            domStyle.set(trpo,"display","none");
            domStyle.set(trpossl,"display","");
            domStyle.set(trpocert,"display","");
        } else {
            domStyle.set(trpo,"display","");
            domStyle.set(trpossl,"display","");
            domStyle.set(trpocert,"display","");
        }
      
    }

    webdavhtauthToggle = function() {

        var select = registry.byId("id_webdav_htauth");
        var password = registry.byId("id_webdav_password");
        var trpassword = password.domNode.parentNode.parentNode;
        var password2 = registry.byId("id_webdav_password2");
        var trpassword2 = password2.domNode.parentNode.parentNode;
        if (select.get('value') == 'none') {
            domStyle.set(trpassword,"display","none");
            domStyle.set(trpassword2,"display","none");
        } else {
            domStyle.set(trpassword,"display","");
            domStyle.set(trpassword2,"display","");
        }

    }

    afpTimemachineToggle = function() {

        var checkbox = registry.byId("id_afp_timemachine");
        var quota = registry.byId("id_afp_timemachine_quota");
        var trquota = quota.domNode.parentNode.parentNode;
        if (checkbox.checked) {
            domStyle.set(trquota, "display", "");
        } else {
            domStyle.set(trquota, "display", "none");
        }

    }

    upsModeToggle = function() {

        var select = registry.byId("id_ups_mode");
        var rh = registry.byId("id_ups_remotehost");
        var rp = registry.byId("id_ups_remoteport");
        var d = registry.byId("id_ups_driver");
        var p = registry.byId("id_ups_port");
        var e = registry.byId("id_ups_extrausers");
        var m = registry.byId("id_ups_rmonitor");
        var o = registry.byId("id_ups_options");
        var h = registry.byId("id_ups_hostsync");
        var trh = rh.domNode.parentNode.parentNode;
        var trp = rp.domNode.parentNode.parentNode;
        var td = d.domNode.parentNode.parentNode;
        var tp = p.domNode.parentNode.parentNode;
        var te = e.domNode.parentNode.parentNode;
        var tm = m.domNode.parentNode.parentNode;
        var to = o.domNode.parentNode.parentNode;
        var th = h.domNode.parentNode.parentNode;
        if(select.get('value') == 'master') {
            domStyle.set(trh, "display", "none");
            domStyle.set(trp, "display", "none");
            domStyle.set(td, "display", "table-row");
            domStyle.set(tp, "display", "table-row");
            domStyle.set(te, "display", "table-row");
            domStyle.set(tm, "display", "table-row");
            domStyle.set(to, "display", "table-row");
            domStyle.set(th, "display", "table-row");
        } else {
            domStyle.set(trp, "display", "table-row");
            domStyle.set(trh, "display", "table-row");
            domStyle.set(trp, "display", "table-row");
            domStyle.set(td, "display", "none");
            domStyle.set(tp, "display", "none");
            domStyle.set(te, "display", "none");
            domStyle.set(tm, "display", "none");
            domStyle.set(to, "display", "none");
            domStyle.set(th, "display", "none");
        }

    }

    initshutdownModeToggle = function() {

        var select = registry.byId("id_ini_type");
        var command = registry.byId("id_ini_command");
        var script = registry.byId("id_ini_script");
        var trc = command.domNode.parentNode.parentNode;
        var trs = script.domNode.parentNode.parentNode;
        if(select.get('value') == 'command') {
            domStyle.set(trs, "display", "none");
            domStyle.set(trc, "display", "table-row");
        } else {
            domStyle.set(trs, "display", "table-row");
            domStyle.set(trc, "display", "none");
        }

    }

    iscsiExtentToggle = function() {

        var select = registry.byId("id_iscsi_target_extent_type");
        var file = registry.byId("id_iscsi_target_extent_path");
        var size = registry.byId("id_iscsi_target_extent_filesize");
        var disk = registry.byId("id_iscsi_target_extent_disk");
        var trf = file.domNode.parentNode.parentNode;
        var trd = disk.domNode.parentNode.parentNode;
        var trs = size.domNode.parentNode.parentNode;
        if(select.get('value') == 'File') {
            domStyle.set(trf, "display", "table-row");
            domStyle.set(trs, "display", "table-row");
            domStyle.set(trd, "display", "none");
        } else {
            domStyle.set(trf, "display", "none");
            domStyle.set(trs, "display", "none");
            domStyle.set(trd, "display", "table-row");
        }

    }

    rebuildDirectoryServiceCache = function(url, sendbtn) {

        sendbtn.set('disabled', true);
        form = getForm(sendbtn);
        data = form.get('value');
        xhr.post(url, {
            handleAs: 'json',
            data: data,
            headers: {"X-CSRFToken": CSRFToken}
        }).then(function(data) {
            sendbtn.set('disabled', false);
            if(!data.error) {
                setMessage(gettext("The cache is being rebuilt."));
            } else {
                setMessage(gettext("The cache could not be rebuilt: ") + data.errmsg, "error");
            }
        });

    };

    sshKeyScan = function(url, sendbtn) {
        sendbtn.set('disabled', true);
        form = getForm(sendbtn);
        data = form.get('value');
        xhr.post(url, {
            handleAs: 'json',
            data: {host: data['repl_remote_hostname'], port: data['repl_remote_port']},
            headers: {"X-CSRFToken": CSRFToken}
        }).then(function(data) {
            sendbtn.set('disabled', false);
            if(!data.error) {
                var key = query("textarea[name=repl_remote_hostkey]", form.domNode);
                key = registry.getEnclosingWidget(key[0]);
                key.set('value', data.key);
            } else {
                Tooltip.show(data.errmsg, sendbtn.domNode);
                on.once(sendbtn.domNode, mouse.leave, function(){
                    Tooltip.hide(sendbtn.domNode);
                });
            }
        });
    };

    vmwareDatastores = function(url, button) {
      var form = getForm(button);
      var data = form.get('value')
      var datastore = registry.byId("id_datastore");
      button.set('disabled', true);

      if(!data['hostname'] || !data['username'] || (!data['password'] && !data['oid'])) {
        if(!data['hostname']) {
          Tooltip.show(gettext("Hostname cannot be blank."), button.domNode);
        }
        if(!data['username']) {
          Tooltip.show(gettext("Username cannot be blank."), button.domNode);
        }
        if(!data['password'] && !data['oid']) {
          Tooltip.show(gettext("Password cannot be blank."), button.domNode);
        }
        on.once(button.domNode, mouse.leave, function() {
          Tooltip.hide(button.domNode);
        });
        button.set('disabled', false);
        return;
      }

      xhr.post(url, {
        data: {
          oid: data['oid'],
          hostname: data['hostname'],
          username: data['username'],
          password: data['password']
        },
        handleAs: "json",
        headers: {"X-CSRFToken": CSRFToken}
      }).then(function(data) {
        if(!data.error) {
          var tempdata = [];
          for(var i=0;i<data.value.length;i++) {
            tempdata.push({name: data.value[i], id: data.value[i]});
          }
          var memory = new Memory({data: tempdata});
          datastore.set('store', memory);
          datastore.loadAndOpenDropDown();
        } else {
          Tooltip.show(data.errmsg, button.domNode);
          on.once(button.domNode, mouse.leave, function() {
            Tooltip.hide(button.domNode);
          });
          var memory = new Memory({data: []});
          datastore.set('store', memory);
          datastore.set('value', '');
        }
        button.set('disabled', false);

      }, function(err) {
         console.log("error", err);
         button.set('disabled', false);
      });
    }

    setMessage = function(msg, css) {

        if(!css) css = "success";
        var footer = dom.byId("messages");
        domConstruct.empty(footer);
        var suc = domConstruct.create("div");
        on(suc, 'click', function() {
            dFx.fadeOut({ node: suc }).play();
        });
        footer.appendChild(suc);
        domClass.add(suc, css);
        html.set(suc, "<p>"+msg+"</p><a style='position: absolute; bottom: 0; right:0; color: white;' href='#'>Dismiss</a>");
        if(css != "error") {
          setTimeout(function() { if(suc) dFx.fadeOut({node: suc}).play();}, 7000);
        }

    };

    serviceFailed = function(srv) {
        var obj = query("img#"+srv+"_toggle");
        if(obj.length > 0) {
            obj = obj[0];
            toggle_service(obj);
        }
    }

    handleJson = function(form, rnode, data) {

        if(data.type == 'page') {
            rnode.set('content', data.content);
        } else if(data.type == 'form') {

            form = registry.byId(data.formid);
            query(".errorlist", form.domNode).forEach(function(item, idx) {
                domConstruct.destroy(item);
            });
            if(data.error == true) {
                var first, field, dom, node;
                for(var key in data.errors) {

                    dom = query("input[name="+key+"],textarea[name="+key+"],select[name="+key+"]", form.domNode);
                    if(dom.length == 0) {
                        dom = query("div[data-dojo-name="+key+"]", form.domNode);
                        if(dom.length != 0) {
                            node = dom[0];
                        } else {
                            console.log("Form element not found: ", key);
                            continue;
                        }
                    } else {
                        field = registry.getEnclosingWidget(dom[0]);
                        var tr = query(field.domNode).closest("tr");
                        if(tr.length > 0) {
                          tr = tr[0];
                          if(domClass.contains(tr, "advancedField")) {
                            var advmode = query(".advModeButton", form.domNode)[0];
                            advmode = registry.getEnclosingWidget(advmode);
                            if(advmode.mode == 'basic') {
                              form.advancedToggle(advmode);
                            }
                          }
                        }
                        if(field) {
                            if(!first && field.focus)
                                first = field;
                            node = field.domNode;
                        }
                    }
                    var ul = domConstruct.create('ul', {style: {display: "none"}}, node.parentNode, "first");
                    domAttr.set(ul, "class", "errorlist");
                    for(var i=0; i<data.errors[key].length;i++) {
                        var li = domConstruct.create('li', {innerHTML: data.errors[key][i]}, ul);
                    }
                    fx.wipeIn({
                        node: ul,
                        duration: 300
                    }).play();

                }

                if(first) first.focus();

            } else {
                //form.reset();
                if(rnode.isInstanceOf(Dialog))
                    rnode.hide();
            }
        } else if(data.type == 'confirm') {

            var confirmdialog = new Dialog({
              id: "editconfirmscary_dialog",
              title: gettext('Confirm'),
              content: data.confirm,
              parseOnLoad: true,
              submitForm: function() {
                form.submitForm(form, null, true);
              },
              onHide: function() {
                  setTimeout(lang.hitch(this, function() {
                      this.destroyRecursive();
                  }), manager.defaultDuration);
              }

            });
            confirmdialog.show();

        } else {

            if(rnode.isInstanceOf(Dialog) && (data.error == false || (data.error == true && !data.type) ) ) {
                rnode.hide();
            }

        }

        if(data.events) {
            for(i=0;i<data.events.length;i++){
                try {
                    eval(data.events[i]);
                } catch(e) {
                    console.log(e);
                }
            }
        }

        if(data.message) {
            setMessage(data.message);
        }


    }

    checkProgressBar = function(pbar, url, uuid, iter) {
        var progress_url;
        if(typeof(url) == 'string') {
             progress_url = url;
        } else {
             progress_url = '/progress';
        }
        if(!iter) iter = 0;
        xhr.get(progress_url, {
            headers: {"X-Progress-ID": uuid}
            }).then(function(data) {
                var obj = eval(data);
                if(obj.state == 'uploading') {
                    var perc = Math.ceil((obj.received / obj.size)*100);
                    if(perc == 100) {
                        pbar.update({'indeterminate': true});
                        return;
                    } else {
                        pbar.update({maximum: 100, progress: perc, indeterminate: false});
                    }
                }
                if(obj.state == 'starting' || obj.state == 'uploading') {
                    if(obj.state == 'starting' && iter >= 3) {
                        return;
                    }
                    setTimeout(function() {
                         checkProgressBar(pbar, url, uuid, iter + 1);
                         }, 1000);
                }
            });
    }

    doSubmit = function(attrs) {
        var pdiv, pbar, pdisplay, uuid, multipart, rnode, newData;

        if(!attrs) {
            attrs = {};
        }

        if(attrs.event !== undefined && attrs.event !== null) {
            // prevent the default submit
            dEvent.stop(attrs.event);
        }

        query('input[type=button],input[type=submit]', attrs.form.domNode).forEach(
            function(inputElem){
                if(inputElem.type == 'submit') {
                    var dj = registry.getEnclosingWidget(inputElem);
                    if(dj) {
                        if(dj.isInstanceOf(dojox.form.BusyButton)) {
                            dj.busyLabel = 'Please wait...';
                        } else {
                            domAttr.set(dj.domNode, "oldlabel", dj.get('label'));
                            dj.set('label', gettext('Please wait...'));
                        }
                    }
                }
                registry.getEnclosingWidget(inputElem).set('disabled',true);
            }
            );

        /* Remove errors from the form */
        query('ul[class=errorlist]', attrs.form.domNode).forEach(function(ul) {
            fx.wipeOut({
                node: ul,
                duration: 300
            }).play();
        });

        newData = attrs.form.get("value");
        newData['__form_id'] = attrs.form.id;
        if(attrs.confirm == true) {
          newData['__confirm'] = "1";
        }
        if(attrs.extraKey) {
          newData[attrs.extraKey] = attrs.extraValue;
        }

        multipart = query("input[type=file]", attrs.form.domNode).length > 0;

        rnode = getDialog(attrs.form);
        if(!rnode) rnode = registry.getEnclosingWidget(attrs.form.domNode.parentNode);

        loadOk = function(data, req) {

            query('input[type=button],input[type=submit]', attrs.form.domNode).forEach(
                  function(inputElem){
                       registry.getEnclosingWidget(inputElem).set('disabled',false);
                   }
                );
            var sbtn = registry.getEnclosingWidget(query('input[type=submit]', attrs.form.domNode)[0]);
            if(sbtn) {
                if(domAttr.has(sbtn.domNode, "oldlabel")) {
                    sbtn.set('label', domAttr.get(sbtn.domNode, "oldlabel"));
                } else {
                    sbtn.set('label', 'Save');
                }
                if(sbtn.isInstanceOf(dojox.form.BusyButton)) sbtn.resetTimeout();
            }
            handleJson(attrs.form, rnode, data);

            if('onComplete' in attrs) {
                attrs.onComplete(data);
            }

        };

        var handleReq = function(data, ioArgs, error) {
            var json;
            if(pbar) {
                pbar.destroy();
                domStyle.set(attrs.form.domNode, "display", "block");
                //rnode.layout();
                rnode._size();
                rnode._position();
            }
            if(pdisplay) {
                pdisplay.destroy();
                domStyle.set(attrs.form.domNode, "display", "block");
                //rnode.layout();
                rnode._size();
                rnode._position();
            }
            if(pdiv) {
                pdiv.destroy();
                domStyle.set(attrs.form.domNode, "display", "block");
                //rnode.layout();
                rnode._size();
                rnode._position();
            }
            try {
                json = JSON.parse(data);
                if(json.error != true && json.error != false) throw "toJson error";
                loadOk(json, ioArgs);
            } catch(e) {
                try {
                    if(!error) {
                        rnode.set('content', data);
                    } else {
                        setMessage(gettext('An error occurred!'), "error");
                        rnode.hide();
                    }
                } catch(e) {}
            }
        };

        if (attrs.progressbar != undefined) {
            var pattrs;
            if(attrs.progressbar == true) {
              pattrs = {
                steps: [
                  {"label": "Uploading file"}
                ],
                fileUpload: true,
                mode: "simple"
              };
            } else if(typeof(attrs.progressbar) == 'string') {
              pattrs = {
                steps: [
                  {"label": "Processing"}
                ],
                fileUpload: false,
                mode: "simple",
                poolUrl: attrs.progressbar
              };
            } else if(attrs.progressbar.mode == 'single') {
              pattrs = {
                steps: attrs.progressbar.steps,
                fileUpload: attrs.progressbar.fileUpload,
                uuid: uuid,
                mode: "single",
                poolUrl: attrs.progressbar.poolUrl
              };
            } else {
              pattrs = {
                steps: attrs.progressbar.steps,
                fileUpload: attrs.progressbar.fileUpload,
                uuid: uuid,
                poolUrl: attrs.progressbar.poolUrl
              };
              if(pattrs.fileUpload === undefined)
                pattrs.fileUpload = true;
            }
            pbar = Progress(pattrs);

             /* We cannot destroy form node, that's why we just hide it
             * otherwise iframe.send won't work, it expects the form domNode
             */
            attrs.form.domNode.parentNode.appendChild(pbar.domNode);
            domStyle.set(attrs.form.domNode, "display", "none");
            //rnode.layout();
            rnode._size();
            rnode._position();

        }

        if( multipart ) {

            uuid = generateRandomUuid();
            iframe.post(attrs.url + '?X-Progress-ID=' + uuid, {
                //form: item.domNode,
                data: {__form_id: attrs.form.id},
                form: attrs.form.id,
                handleAs: 'text',
                headers: {"X-CSRFToken": CSRFToken}
                }).then(function(response) {

                    if(attrs.longRunning && attrs.longRunningUrl) {
                      waitForComplete = function() {
                        var longpromise = xhr.post(attrs.longRunningUrl + '?uuid=' + response, {
                          headers: {"X-CSRFToken": CSRFToken},
                          handleAs: 'text'
                        });
                        longpromise.then(function(data) {
                           longpromise.response.then(function(response) {
                            if(response.status == 202) {
                              setTimeout(waitForComplete, 2000);
                            } else {
                              handleReq(data);
                            }
                           });
                        });
                      }
                      setTimeout(waitForComplete, 2000);
                    } else {
                      try {
                          JSON.parse(response);
                      }
                      catch (e) {
                          response = "<pre>" + response + "</pre>";
                      }
                      handleReq(response);
                    }
                }, function(evt) {
                    handleReq(evt.response.data, evt.response, true);
                });

        } else {

            var promise = xhr.post(attrs.url, {
                data: newData,
                handleAs: 'text',
                headers: {"X-CSRFToken": CSRFToken}
            });
            promise.then(
                function(data) {
                  promise.response.then(function(response) {
                    if(attrs.longRunning && response.status == 202) {
                      waitForComplete = function() {
                        var longpromise = xhr.post(attrs.url + '?uuid=' + data, {
                          headers: {"X-CSRFToken": CSRFToken},
                          handleAs: 'text'
                        });
                        longpromise.then(function(data) {
                           longpromise.response.then(function(response) {
                            if(response.status == 202) {
                              setTimeout(waitForComplete, 2000);
                            } else {
                              handleReq(data);
                            }
                           });
                        });
                      }
                      setTimeout(waitForComplete, 2000);
                    } else {
                      handleReq(data);
                    }
                  });
                },
                function(evt) {
                    handleReq(evt.response.data, evt.response, true);
                }
            );

        }

        if (attrs.progressbar != undefined) {
            pbar.update(uuid);

        }
    }

    checkNumLog = function(unselected) {
        var num = 0;
        for(var i=0;i<unselected.length;i++) {
            var q = query("input[name=zpool_"+unselected[i]+"]:checked");
            if(q.length > 0) {
                if(q[0].value == 'log')
                num += 1;
            }
        }

        var lowlog = dom.byId("lowlog");
        if(!lowlog) return;

        if(num == 1) {
            domStyle.set(lowlog, "display", "");
        } else {
            domStyle.set(lowlog, "display", "none");
        }
    }

    taskrepeat_checkings = function() {

        var repeat = registry.byId("id_task_repeat_unit");
        wk = query(registry.byId('id_task_byweekday_0').domNode).parents("tr").first()[0];
        if(repeat.get('value') != 'weekly') {
            domStyle.set(wk, "display", "none");
        } else {
            domStyle.set(wk, "display", "");
        }

    }

    checked_zfs_extra_option = function(disk, radio_type) {
        // Returns whether the radio button is checked
        let radio_input = query("input[name=zpool_" + disk + "]:input[value=" + radio_type + "]");
        if (radio_input.length > 0) {
            return [radio_input[0].checked, true];
        } else {
            return [false, false];
        }
    }

    zfswizardcheckings = function(vol_change, first_load) {

        if(!registry.byId("wizarddisks")) return;
        var add = registry.byId("id_volume_add");
        var add_mode = false;
        if(add && add.get("value") != '') {
            add_mode = true;
        }
        var disks = registry.byId("wizarddisks");
        var enc = registry.byId("id_enc");
        //var encini = registry.byId("id_encini");
        var d = disks.get('value');
        html.set(dom.byId("wizard_num_disks"), d.length + '');

        registry.byId("id_volume_name").set('disabled', add_mode);

        if(enc.get("value") == 'on' && !add_mode) {
          //encini.set('disabled', false);
          enc.set('disabled', false);
        } else {
          enc.set('disabled', add_mode);
          //encini.set('disabled', true);
        }

        if(vol_change == true) {
            var unselected = [];
            disks.invertSelection(null);
            var opts = disks.get("value");
            for(var i=0;i<opts.length;i++) {
                unselected.push(opts[i]);
            }
            disks.invertSelection(null);

            if(unselected.length > 0) {

                var tab = dom.byId("disks_unselected");
                var txt = "";
                var toappend = [];
                for(var i=0;i<unselected.length;i++) {
                    var tr = domConstruct.create("tr");
                    var td = domConstruct.create("td", {innerHTML: unselected[i]});
                    tr.appendChild(td);

                    let radio_name = "zpool_" + unselected[i];
                    let checked = checked_zfs_extra_option(unselected[i], "none");

                    if (checked[1] == false) {
                        // if none does not exist, we would like to make sure that checked is true for none
                        checked[0] = true;
                    }
                    var td = domConstruct.create("td");
                    var rad = new RadioButton({ checked: checked[0], value: "none", name: radio_name});
                    on(rad, 'click', function() {checkNumLog(unselected);});
                    on(rad, 'change', function() {zfsextrawizardcheckings(this);});
                    td.appendChild(rad.domNode);
                    tr.appendChild(td);

                    checked = checked_zfs_extra_option(unselected[i], "log");

                    var td = domConstruct.create("td");
                    var rad = new RadioButton({ checked: checked[0], value: "log", name: radio_name});
                    on(rad, 'click', function() {checkNumLog(unselected);});
                    td.appendChild(rad.domNode);
                    tr.appendChild(td);

                    checked = checked_zfs_extra_option(unselected[i], "cache");

                    var td = domConstruct.create("td");
                    var rad = new RadioButton({ checked: checked[0], value: "cache", name: radio_name});
                    on(rad, 'click', function() {checkNumLog(unselected);});
                    td.appendChild(rad.domNode);
                    tr.appendChild(td);

                    checked = checked_zfs_extra_option(unselected[i], "spare");

                    var td = domConstruct.create("td");
                    var rad = new RadioButton({ checked: checked[0], value: "spare", name: radio_name});
                    on(rad, 'click', function() {checkNumLog(unselected);});
                    td.appendChild(rad.domNode);
                    tr.appendChild(td);

                    toappend.push(tr);
                }

                query("#disks_unselected tbody tr").orphan();

                for(var i=0;i<toappend.length;i++) {
                    dojo.place(toappend[i], query("#disks_unselected tbody")[0]);
                }

               domStyle.set("zfsextra", "display", "");

            } else {
                if(first_load == true) {
                    domStyle.set("zfsextra", "display", "");
                } else {
                    query("#disks_unselected tbody tr").orphan();
                    domStyle.set("zfsextra", "display", "none");
                }
            }
        }

        if(d.length >= 2) {
            domStyle.set("grpopt", "display", "");
        } else {
            domStyle.set("grpopt", "display", "none");
            query("input[name=group_type]:checked").forEach(function(tag) {
                var dtag = registry.getEnclosingWidget(tag);
                if(dtag) dtag.set('checked', false);
            });
        }

        domStyle.set('zfsdedup', 'display', 'table-row');

        if(d.length >= 3) {
            domStyle.set("grpraidz", "display", "block");
        } else {
            domStyle.set("grpraidz", "display", "none");
        }

        if(d.length >= 4) {
            domStyle.set("grpraidz2", "display", "block");
        } else {
            domStyle.set("grpraidz2", "display", "none");
        }

        if(d.length >= 5) {
            domStyle.set("grpraidz3", "display", "block");
        } else {
            domStyle.set("grpraidz3", "display", "none");
        }

    }

    zfsextrawizardcheckings = function(selected_radio_disk) {
        let name = selected_radio_disk.name.replace("zpool_", "");
        let disk_option = query("option[value=" + name + "]");

        if (disk_option.length > 0) {
            disk_option = disk_option[0];
            if(selected_radio_disk.checked) {
                // add this option to disks
                domStyle.set(disk_option, "display", "");

            } else {
                // remove this option from disks
                domStyle.set(disk_option, "display", "none");
            }
        }
    }

    wizardcheckings = function(vol_change, first_load) {

        if(!registry.byId("wizarddisks")) return;

        var disks = registry.byId("wizarddisks");
        var d = disks.get('value');
        html.set(dom.byId("wizard_num_disks"), d.length + '');

        if(d.length >= 2) {
            domStyle.set("grpopt", "display", "");
        } else {
            domStyle.set("grpopt", "display", "none");
            query("input[name=group_type]:checked").forEach(function(tag) {
                var dtag = registry.getEnclosingWidget(tag);
                if(dtag) dtag.set('checked', false);
            });
        }

        if(d.length-1 >= 2 && (((d.length-2)&(d.length-1)) == 0)) {
            domStyle.set("grpraid3", "display", "block");
        } else {
            domStyle.set("grpraid3", "display", "none");
        }

    }

    getDialog = function(from) {

        var turn = from;
        while(1) {
            turn = registry.getEnclosingWidget(turn.domNode.parentNode);
            if(turn == null) return null;
            if(turn.isInstanceOf(Dialog)) break;
        }
        return turn;

    };

    getForm = function(from) {

        var turn = from;
        while(1) {
            turn = registry.getEnclosingWidget(turn.domNode.parentNode);
            if(turn.isInstanceOf(Form)) break;
        }
        return turn;

    };

    cancelDialog = function(from) {

        var dialog = getDialog(from);
        canceled = true;
        dialog.hide();

    };

    submitCertificateForm = function(btn_id) {
        dom.byId(btn_id).click();
    };

    refreshById = function(id) {
        registry.byId(id).refresh();
    };

    refreshTree = function() {
        var fadeArgs = {
           node: "fntree",
           onEnd: function() { registry.byId("fntree").reload(); }
         };
        dFx.fadeOut(fadeArgs).play();
    }

    refreshTabs = function(nodes) {
        if(nodes && canceled == false) {
            refreshTree();
            dArray.forEach(nodes, function(entry, i) {
                if(entry.isInstanceOf && entry.isInstanceOf(ContentPane)) {
                    entry.refresh();
                    var par = registry.getEnclosingWidget(entry.domNode.parentNode);
                    par.selectChild(entry);
                    var par2 = registry.getEnclosingWidget(par.domNode.parentNode);
                    if(par2 && par2.isInstanceOf(ContentPane))
                        registry.byId("content").selectChild(par2);
                } else {
                    if(entry.domNode) entry = entry.domNode;
                    var par = query(entry).parents(".objrefresh").first()[0];
                    var cp = registry.getEnclosingWidget(par);
                    if(cp) cp.refresh();
                }
            });

        }
    }

    __stack = [];
    addToStack = function(f) {
        __stack.push(f);
    }

    processStack = function() {
        while(__stack.length > 0) {
            f = __stack.pop();
            try {
                f();
            } catch(e) {
                console.log(e);
            }
        }

    }

    commonDialog = function(attrs) {
        canceled = false;
        dialog = new Dialog({
            id: attrs.id,
            title: attrs.name,
            href: attrs.url,
            parseOnLoad: true,
            closable: true,
            style: attrs.style,
            onHide: function() {
                setTimeout(lang.hitch(this, function() {
                    this.destroyRecursive();
                }), manager.defaultDuration);
                refreshTabs(attrs.nodes);
            },
            onLoad: function() {
                processStack();
                //this.layout(); // dojo 1.7
                this._position(); // dojo 1.8
            },
            onCancel: function() {
                canceled = true;
                this.hide();
            }
        });
        if(attrs.onLoad) {
            f = lang.hitch(dialog, attrs.onLoad);
            f();
        }
        dialog.show();
    };

    editObject = function(name, url, nodes, onload) {
        commonDialog({
            id: "edit_dialog",
            style: "max-width: 75%;max-height:70%;background-color:white;overflow:auto;",
            name: name,
            url: url,
            nodes: nodes,
            onLoad: onload
            });
    }

    configDownloadObject = function(name, url) {
        commonDialog({
            id: "config_download_dialog",
            style: "max-width: 75%;max-height:70%;background-color:white;overflow:auto;",
            name: name,
            url: url,
            });
    }

    editScaryObject = function(name, url, nodes) {
        commonDialog({
            id: "editscary_dialog",
            style: "max-width: 75%;max-height:70%;background-color:white;overflow:auto;",
            name: name,
            url: url,
            nodes: nodes
            });
    };

    volumeWizard = function(name, url, nodes) {
        commonDialog({
            id: "wizard_dialog",
            style: "max-width: 650px;min-height:200px;max-height:500px;background-color:white;overflow:auto;",
            name: name,
            url: url,
            nodes: nodes
            });
    }

    jobLogs = function(job_id) {
        commonDialog({
            id: "job_logs_dialog",
            style: "max-width: 75%;max-height:70%;background-color:white;overflow:auto;",
            name: "Logs",
            url: "/legacy/system/job/" + job_id + "/logs/",
            nodes: []
        });
    }

    viewModel = function(name, url, tab) {
        var opened = false;
        var p = registry.byId("content");
        var c = p.getChildren();
        for(var i=0; i<c.length; i++){
            if(c[i].title == name){
                c[i].href = url;
                p.selectChild(c[i]);
                c[i].refresh();
                opened = true;
            } else {
                p.removeChild(c[i]);
                c[i].destroy();
            }
        }
        if(opened !== true) {
            var pane = new ContentPane({
                href: url,
                title: name,
                closable: false,
                parseOnLoad: true,
                refreshOnShow: true
            });
            if(tab)
                pane.tab = tab;
            domClass.add(pane.domNode, "objrefresh" );
            p.addChild(pane);
            p.selectChild(pane);
        }
    }

    confirmRrdDestroyHide = function() {
        domStyle.set(registry.byId("id_confirm_rrd_destroy").domNode.parentNode.parentNode, "display", "none");
    }

    confirmRrdDestroyShow = function() {
        domStyle.set(registry.byId("id_confirm_rrd_destroy").domNode.parentNode.parentNode, "display", "");
    }

    disclosureToggle = function(element) {
        child = document.querySelector(".disclosure-content");
        if ( element.className === "disclosure-title" ) {
            element.className = "disclosure-title active"
            child.className = "disclosure-content show";
        } else {
            element.className = "disclosure-title";
            child.className = "disclosure-content";
        }
    }

    dojo._contentHandlers.text = (function(old){
      return function(xhr){
        if(xhr.responseText.match("<!-- THIS IS A LOGIN WEBPAGE -->")){
          window.location='/legacy/';
          return '';
        }
        var text = old(xhr);
        return text;
      }
    })(dojo._contentHandlers.text);

    ready(function() {

        menuSetURLs();
        Menu.openSystem();
        var store = new JsonRestStore({
            target: Menu.urlTree,
            labelAttribute: "name"
        });

        var treeModel = new ForestStoreModel({
            store: store,
            query: {},
            rootId: "root",
            rootLabel: "FreeNAS",
            childrenAttrs: ["children"]
        });

        var treeclick = function(item) {
            var p = registry.byId("content");

            if(item.type == 'object' ||
               item.type == 'dialog' ||
               item.type == 'scary_dialog' ||
               item.type == 'editobject' ||
               item.type == 'volumewizard'
                ) {
                var data = query(".data_"+item.app_name+"_"+item.model);
                var func;

                if(item.type == 'volumewizard') func = volumeWizard;
                else if(item.type == 'scary_dialog') func = editScaryObject;
                else func = editObject;

                if(data) {
                    widgets = [];
                    data.forEach(function(item, idx) {
                        widget = registry.getEnclosingWidget(item);
                        if(widget) {
                            widgets.push(widget);
                        }
                    });
                    func(item.name, item.url, widgets);
                } else
                    func(item.name, item.url);

            } else if(item.type == 'opensystem') {
                Menu.openSystem(item.gname);
            } else if(item.type == 'opentasks') {
                Menu.openTasks(item.gname);
            } else if(item.type == 'openvm') {
                Menu.openVM(item.gname);
            } else if(item.type == 'opennetwork') {
                Menu.openNetwork(item.gname);
            } else if(item.type == 'en_dis_services') {
                Menu.openServices();
            } else if(item.type == 'openjails') {
                Menu.openJails(item.gname);
            } else if(item.type == 'openplugins') {
                Menu.openPlugins(item.gname);
            } else if(item.type == 'pluginsfcgi') {
                Menu.openPluginsFcgi(p, item);
            } else if(item.type == 'opendirectoryservice') {
                Menu.openDirectoryService(item.gname);
            } else if(item.type == 'openaccount') {
                Menu.openAccount(item.gname);
            } else if(item.type == 'iscsi') {
                Menu.openISCSI(item.gname);
            } else if(item.action == 'logout') {
                window.location='/legacy/account/logout/';
            } else if(item.action == 'displayprocs') {
                registry.byId("top_dialog").show();
            } else if(item.action == 'shell') {
                _webshell = new WebShell();
            } else if(item.action == 'wizard') {
                editObject(gettext("Wizard"), wizardUrl, []);
            } else if(item.action == 'opensupport') {
                Menu.openSupport();
            } else if(item.action == 'opendocumentation') {
                Menu.openDocumentation();
            } else if(item.type == 'opensharing') {
                Menu.openSharing(item.gname);
            } else if(item.type == 'openstorage') {
                Menu.openStorage(item.gname);
            } else if(item.type == 'viewmodel') {
                //  get the children and make sure we haven't opened this yet.
                var opened = false;
                var c = p.getChildren();
                for(var i=0; i<c.length; i++){
                    if(c[i].title == item.name){
                        p.selectChild(c[i]);
                        c[i].refresh();
                        opened = true;
                    } else {
                        p.removeChild(c[i]);
                        c[i].destroy();
                    }
                }
                if(opened !== true) {
                    var pane = new ContentPane({
                        id: "data_"+item.app_name+"_"+item.model,
                        href: item.url,
                        title: item.name,
                        closable: false,
                        refreshOnShow: true,
                        parseOnLoad: true
                    });
                    p.addChild(pane);
                    domClass.add(pane.domNode, ["objrefresh","data_"+item.app_name+"_"+item.model] );
                    p.selectChild(pane);
                }
            } else {
                //  get the children and make sure we haven't opened this yet.
                var opened = false;
                var c = p.getChildren();
                for(var i=0; i<c.length; i++){
                    if(c[i].tab == item.gname){
                        p.selectChild(c[i]);
                        c[i].refresh();
                        opened = true;
                    } else {
                        p.removeChild(c[i]);
                        c[i].destroy();
                    }
                }
                if(opened !== true) {
                    var pane = new ContentPane({
                        href: item.url,
                        title: item.name,
                        closable: false,
                        parseOnLoad: true
                    });
                    pane.tab = item.gname;
                    domClass.add(pane.domNode, ["objrefresh","data_"+item.app_name+"_"+item.model] );
                    p.addChild(pane);
                    p.selectChild(pane);
                }
            }

        };

        mytree = new Tree({
            id: "fntree",
            model: treeModel,
            showRoot: false,
            persist: true,
            onClick: treeclick,
            onLoad: function() {
                var fadeArgs = {
                   node: "fntree"
                 };
                dFx.fadeIn(fadeArgs).play();
            },
            openOnClick: true,
            getIconClass: function(item, opened) {
                if(item.icon && item.icon.search("/") == -1)
                    return item.icon;
            },
            getIconStyle: function(item, opened) {
                if(item.icon && item.icon.search("/") != -1)
                    return {
                        backgroundImage: "url("+item.icon+")",
                        height: '16px',
                        width: '16px'
                        };
            }
        });
        registry.byId("menupane").set('content', mytree);
        if(wizardShow) editObject(gettext("Initial Wizard"), wizardUrl, []);

    });

    autodetectFs = function() {
        var disk = registry.byId("id_volume_disks").get("value");
        if (disk) {
            var radio = registry.byId("id_volume_fstype_0");
            var label = radio.domNode.parentNode.childNodes[1];

            radio.set("disabled", true);
            label.textContent = " Autodetecting...";

            Middleware.call('pool.import_disk_autodetect_fs_type', ["/dev/" + disk], function(result) {
                label.textContent = " Autodetected (" + result.toUpperCase() + ")";
                radio.set("disabled", false);
                radio.set("value", result.toUpperCase());
            }, function(error) {
                label.textContent = " Autodetect failed: " + error.reason;
            });
        }
    }
});
