/*
 * ProxLB plugin for the Proxmox VE web interface.
 *
 * Adds "Datacenter -> ProxLB" with:
 *   - live node usage and the current balance spread,
 *   - the last ProxLB run (planned or performed migrations),
 *   - buttons for a dry-run preview, a real rebalance and automatic balancing.
 *
 * The data comes from the Notes of the guest tagged "proxlb" (written by
 * proxlb-agent) and from /cluster/resources, using the logged-in user's
 * own session. Commands are sent by adding tags to that guest. The guest is
 * the ProxLB container itself, or - when ProxLB runs on another machine -
 * a small "anchor" VM without disks (see proxlb-external.sh).
 *
 * Installed by contrib/proxmox/gui/install-gui.sh. License: GPL-3.0
 */
/* global Ext, Proxmox, PVE, gettext */
(function() {
    // NOTE: no 'use strict' here - ExtJS callParent() relies on fn.caller,
    // which strict mode forbids (it would break the whole Datacenter panel).

    if (typeof Ext === 'undefined' || typeof PVE === 'undefined' || !PVE.panel || !PVE.dc) {
        return; // not the PVE desktop UI (e.g. mobile UI)
    }

    const PLB = {
        VERSION: '1.3.1',
        TAG_CT: 'proxlb',
        TAG_ACTIVE: 'proxlb-active',
        TAG_CMD_DRYRUN: 'proxlb-cmd-dryrun',
        TAG_CMD_RUN: 'proxlb-cmd-run',
        MARKER: '<!--proxlb-status:',
        STALE_SECONDS: 15 * 60,
        REFRESH_MS: 10000,

        splitTags: function(tags) {
            return (tags || '').split(/[;, ]/).filter((t) => t !== '');
        },

        parseStatus: function(description) {
            if (!description) {
                return null;
            }
            let start = description.indexOf(PLB.MARKER);
            if (start < 0) {
                return null;
            }
            start += PLB.MARKER.length;
            let end = description.indexOf('-->', start);
            if (end < 0) {
                return null;
            }
            try {
                return JSON.parse(description.substring(start, end));
            } catch (e) {
                return null;
            }
        },

        fmtTime: function(ts) {
            if (!ts) {
                return '-';
            }
            return Ext.Date.format(new Date(ts * 1000), 'Y-m-d H:i');
        },

        ago: function(ts) {
            if (!ts) {
                return '';
            }
            let s = Math.max(0, Math.round(Date.now() / 1000 - ts));
            if (s < 90) {
                return s + 's ago';
            }
            if (s < 5400) {
                return Math.round(s / 60) + ' min ago';
            }
            if (s < 172800) {
                return Math.round(s / 3600) + ' h ago';
            }
            return Math.round(s / 86400) + ' days ago';
        },

        bar: function(pct, warnAt) {
            if (pct === null || pct === undefined || isNaN(pct)) {
                return '-';
            }
            let p = Math.max(0, Math.min(100, pct));
            let color = p >= (warnAt || 85) ? '#d9534f' : p >= 70 ? '#f0ad4e' : '#5cb85c';
            return '<div style="display:flex;align-items:center;gap:6px">' +
                '<div style="flex:1;height:10px;border-radius:3px;background:rgba(128,128,128,0.25);overflow:hidden">' +
                '<div style="width:' + p.toFixed(1) + '%;height:100%;background:' + color + '"></div></div>' +
                '<span style="min-width:44px;text-align:right">' + p.toFixed(1) + ' %</span></div>';
        },

        badge: function(text, color) {
            return '<span style="display:inline-block;padding:1px 7px;border-radius:9px;color:#fff;' +
                'background:' + color + ';font-size:11px;font-weight:600;line-height:16px;vertical-align:middle">' + Ext.htmlEncode(text) + '</span>';
        },
    };
    PVE.ProxLB = PLB;

    Ext.define('PVE.dc.ProxLBView', {
        extend: 'Ext.panel.Panel',
        alias: 'widget.pveDcProxLBView',

        onlineHelp: undefined,
        layout: { type: 'vbox', align: 'stretch' },
        scrollable: 'y',
        bodyPadding: 10,
        defaults: { margin: '0 0 10 0' },

        // runtime state
        ct: null,
        status: null,
        ctTags: [],
        resources: [],

        initComponent: function() {
            let me = this;

            me.nodeStore = Ext.create('Ext.data.Store', {
                fields: ['node', 'online', 'maintenance', 'cpu', 'mem', 'disk', 'guests',
                    'lastMem', 'lastCpu', 'out', 'in'],
                sorters: 'node',
            });
            me.moveStore = Ext.create('Ext.data.Store', {
                fields: ['vmid', 'name', 'type', 'from', 'to'],
                sorters: ['from', 'vmid'],
            });

            me.summary = Ext.create('Ext.Component', {
                html: gettext('Loading') + ' ...',
                style: { 'line-height': '1.8em' },
            });

            me.btnPreview = Ext.create('Ext.button.Button', {
                text: gettext('Preview (dry-run)'),
                iconCls: 'fa fa-eye',
                disabled: true,
                handler: () => me.sendCommand(PLB.TAG_CMD_DRYRUN),
            });
            me.btnRun = Ext.create('Ext.button.Button', {
                text: gettext('Rebalance now'),
                iconCls: 'fa fa-balance-scale',
                disabled: true,
                handler: function() {
                    Ext.Msg.confirm(gettext('Confirm'),
                        gettext('Let ProxLB migrate guests now to balance the cluster?'),
                        (btn) => { if (btn === 'yes') { me.sendCommand(PLB.TAG_CMD_RUN); } });
                },
            });
            me.btnAuto = Ext.create('Ext.button.Button', {
                text: gettext('Automatic balancing'),
                iconCls: 'fa fa-toggle-off',
                disabled: true,
                handler: () => me.toggleAuto(),
            });
            me.btnCt = Ext.create('Ext.button.Button', {
                text: gettext('Open container'),
                iconCls: 'fa fa-cube',
                disabled: true,
                handler: function() {
                    let tree = Ext.ComponentQuery.query('pveResourceTree')[0];
                    if (tree && me.ct) {
                        tree.selectById(me.ct.type + '/' + me.ct.vmid);
                    }
                },
            });

            me.tbar = [
                me.btnPreview, me.btnRun, '-', me.btnAuto, '->', me.btnCt,
                {
                    text: gettext('Reload'),
                    iconCls: 'fa fa-refresh',
                    handler: () => me.reload(),
                },
            ];

            me.items = [
                {
                    xtype: 'fieldset',
                    title: gettext('Status'),
                    items: [me.summary],
                },
                {
                    xtype: 'grid',
                    title: gettext('Nodes'),
                    store: me.nodeStore,
                    disableSelection: true,
                    columns: [
                        { header: gettext('Node'), dataIndex: 'node', width: 140,
                            renderer: function(v, md, rec) {
                                let icon = rec.data.online ? 'good fa-circle' : 'critical fa-times-circle';
                                let txt = '<i class="fa ' + icon + '"></i> ' + Ext.htmlEncode(v);
                                if (rec.data.maintenance) {
                                    txt += ' ' + PLB.badge('maintenance', '#f0ad4e');
                                }
                                return txt;
                            } },
                        { header: gettext('CPU') + ' (live)', dataIndex: 'cpu', flex: 1,
                            renderer: (v) => PLB.bar(v) },
                        { header: gettext('Memory') + ' (live)', dataIndex: 'mem', flex: 1,
                            renderer: (v) => PLB.bar(v) },
                        { header: gettext('Memory') + ' @ ' + gettext('last run'), dataIndex: 'lastMem', width: 160,
                            renderer: (v) => v === null || v === undefined ? '-' : v.toFixed(1) + ' %' },
                        { header: gettext('Guests'), dataIndex: 'guests', width: 80, align: 'right' },
                        { header: gettext('Moves out / in'), width: 120, align: 'center',
                            renderer: (v, md, rec) => (rec.data.out || 0) + ' / ' + (rec.data.in || 0) },
                    ],
                },
                {
                    xtype: 'grid',
                    itemId: 'moves',
                    title: gettext('Migrations of the last run'),
                    store: me.moveStore,
                    minHeight: 120,
                    // fixed text: changing it later needs a refresh of the grid view, which breaks the
                    // panel layout before the grid has a size (blank tab) - the title says it
                    emptyText: gettext('No migrations to show.'),
                    viewConfig: { deferEmptyText: false },
                    columns: [
                        { header: 'ID', dataIndex: 'vmid', width: 80 },
                        { header: gettext('Name'), dataIndex: 'name', flex: 1, renderer: Ext.htmlEncode },
                        { header: gettext('Type'), dataIndex: 'type', width: 70,
                            renderer: (v) => v === 'ct' ? 'CT' : 'VM' },
                        { header: gettext('From'), dataIndex: 'from', width: 140, renderer: Ext.htmlEncode },
                        { header: '', width: 40, align: 'center',
                            renderer: () => '<i class="fa fa-long-arrow-right"></i>' },
                        { header: gettext('To'), dataIndex: 'to', width: 140, renderer: Ext.htmlEncode },
                    ],
                },
                {
                    xtype: 'component',
                    html: '<span style="opacity:0.7">' +
                        gettext('Tip: exclude a guest with the tag') + ' <code>plb_ignore_vm</code>, ' +
                        gettext('keep guests together with') + ' <code>plb_affinity_&lt;group&gt;</code>, ' +
                        gettext('spread them with') + ' <code>plb_anti_affinity_&lt;group&gt;</code>, ' +
                        gettext('pin with') + ' <code>plb_pin_&lt;node&gt;</code>. ProxLB GUI ' + PLB.VERSION +
                        '</span>',
                },
            ];

            me.callParent();

            me.on('activate', () => me.startPolling());
            me.on('deactivate', () => me.stopPolling());
            me.on('destroy', () => me.stopPolling());
        },

        startPolling: function() {
            let me = this;
            me.stopPolling();
            me.reload();
            me.timer = setInterval(() => me.reload(), PLB.REFRESH_MS);
        },

        stopPolling: function() {
            let me = this;
            if (me.timer) {
                clearInterval(me.timer);
                me.timer = null;
            }
        },

        reload: function() {
            let me = this;
            if (me.loading) {
                return;
            }
            me.loading = true;
            Proxmox.Utils.API2Request({
                url: '/cluster/resources',
                method: 'GET',
                success: function(response) {
                    me.resources = response.result.data || [];
                    let cts = me.resources.filter((r) => (r.type === 'lxc' || r.type === 'qemu') &&
                        PLB.splitTags(r.tags).indexOf(PLB.TAG_CT) !== -1);
                    cts.sort((a, b) => a.vmid - b.vmid);
                    me.ct = cts.length ? cts[0] : null;
                    if (!me.ct) {
                        me.loading = false;
                        me.status = null;
                        me.ctTags = [];
                        me.render_();
                        return;
                    }
                    me.ctTags = PLB.splitTags(me.ct.tags);
                    Proxmox.Utils.API2Request({
                        url: '/nodes/' + me.ct.node + '/' + me.ct.type + '/' + me.ct.vmid + '/config',
                        method: 'GET',
                        success: function(resp) {
                            me.loading = false;
                            me.status = PLB.parseStatus(resp.result.data.description);
                            me.render_();
                        },
                        failure: function(resp) {
                            me.loading = false;
                            me.status = null;
                            me.render_(resp.htmlStatus);
                        },
                    });
                },
                failure: function(response) {
                    me.loading = false;
                    me.render_(response.htmlStatus);
                },
            });
        },

        render_: function(error) {
            let me = this;
            if (me.isDestroyed || me.destroyed) {
                return;
            }
            let st = me.status || {};
            let ct = me.ct;
            let auto = me.ctTags.indexOf(PLB.TAG_ACTIVE) !== -1;
            let queued = me.ctTags.indexOf(PLB.TAG_CMD_RUN) !== -1 ||
                me.ctTags.indexOf(PLB.TAG_CMD_DRYRUN) !== -1;
            let running = st.state === 'running';
            // no heartbeat is written while a (long) balancing run is in progress
            let stale = !running && st.hb && (Date.now() / 1000 - st.hb) > PLB.STALE_SECONDS;
            let ctRunning = ct && ct.status === 'running';
            // ProxLB on another machine: the guest is an anchor VM that never runs
            let external = !!ct && ct.type === 'qemu';
            let agentAlive = running || (!!st.hb && (Date.now() / 1000 - st.hb) <= PLB.STALE_SECONDS);

            // ---- nodes -------------------------------------------------------
            let method = st.method || 'memory';
            let idx = { memory: 0, cpu: 1, disk: 2 }[method] || 0;
            let lastNodes = st.nodes || {};
            let moveOut = {}, moveIn = {};
            (st.moves || []).forEach((m) => {
                moveOut[m[3]] = (moveOut[m[3]] || 0) + 1;
                moveIn[m[4]] = (moveIn[m[4]] || 0) + 1;
            });
            let guestsPerNode = {};
            me.resources.forEach((r) => {
                if ((r.type === 'qemu' || r.type === 'lxc') && r.status === 'running' && !r.template) {
                    guestsPerNode[r.node] = (guestsPerNode[r.node] || 0) + 1;
                }
            });
            let nodes = me.resources.filter((r) => r.type === 'node').map((n) => {
                let last = lastNodes[n.node];
                let online = n.status === 'online';
                return {
                    node: n.node,
                    online: online,
                    maintenance: last ? last[3] === 1 : false,
                    cpu: online && n.maxcpu ? n.cpu * 100 : null,
                    mem: online && n.maxmem ? n.mem / n.maxmem * 100 : null,
                    disk: online && n.maxdisk ? n.disk / n.maxdisk * 100 : null,
                    guests: guestsPerNode[n.node] || 0,
                    lastMem: last ? last[0] : null,
                    lastCpu: last ? last[1] : null,
                    out: moveOut[n.node] || 0,
                    in: moveIn[n.node] || 0,
                };
            });
            me.nodeStore.loadData(nodes);

            // balance spread (live) for the configured method
            let values = nodes.filter((n) => n.online && !n.maintenance)
                .map((n) => [n.mem, n.cpu, n.disk][idx])
                .filter((v) => v !== null && v !== undefined);
            let spread = values.length > 1 ? Math.max(...values) - Math.min(...values) : 0;
            // nothing reported yet: no threshold, no verdict, no "balanced" claims
            let hasRun = !!st.finished;
            let balanciness = st.balanciness;

            // ---- moves -------------------------------------------------------
            me.moveStore.loadData((st.moves || []).map((m) => ({
                vmid: m[0], name: m[1], type: m[2], from: m[3], to: m[4],
            })));
            let movesGrid = me.down('#moves');
            if (movesGrid) {
                let title = !hasRun ? gettext('Migrations of the last run') + ' - ' + gettext('no run yet')
                    : st.dry_run ? gettext('Planned migrations (preview of last run)')
                        : gettext('Migrations performed by last run');
                if (hasRun && st.ok && !st.moves_total) {
                    title += ' - ' + gettext('none, the cluster is balanced');
                }
                if (st.moves_total > (st.moves || []).length) {
                    title += ' - ' + Ext.String.format(gettext('showing {0} of {1}'),
                        (st.moves || []).length, st.moves_total);
                }
                if (movesGrid.title !== title) {
                    movesGrid.setTitle(title);
                }
            }

            // ---- summary -----------------------------------------------------
            let lines = [];
            if (error) {
                lines.push(PLB.badge(gettext('Error'), '#d9534f') + ' ' + error);
            }
            if (!ct) {
                lines.push(PLB.badge(gettext('not installed'), '#777') + ' ' +
                    gettext('No container or VM with the tag') + ' <code>proxlb</code> ' + gettext('found.') + ' ' +
                    gettext('Create a ProxLB container with') + ' <code>proxlb-lxc</code>, ' +
                    gettext('or connect ProxLB on another machine with') + ' <code>proxlb-external</code>.');
            } else {
                let haOk = ct.hastate === 'started' || (external && ct.hastate === 'stopped');
                let haState = ct.hastate
                    ? PLB.badge('HA: ' + ct.hastate, haOk ? '#5cb85c' : '#f0ad4e')
                    : PLB.badge(gettext('HA: off'), '#777');
                let haHint = external
                    ? 'ha-manager add vm:' + ct.vmid + ' --state stopped'
                    : 'proxlb-lxc --enable-ha ' + ct.vmid;
                let agentState = stale ? PLB.badge(gettext('agent not responding'), '#d9534f')
                    : !st.hb ? PLB.badge(gettext('waiting for agent'), '#f0ad4e') : '';
                if (external) {
                    lines.push('<b>ProxLB:</b> ' + gettext('runs on') + ' <b>' +
                        Ext.htmlEncode(st.host || '?') + '</b> ' + PLB.badge(gettext('external'), '#337ab7') +
                        ' ' + agentState + ' &middot; ' + gettext('status via anchor VM') + ' ' + ct.vmid +
                        ' (' + Ext.htmlEncode(ct.name || '') + ') on ' + Ext.htmlEncode(ct.node) + ' ' + haState);
                } else {
                    let ctState = ctRunning ? PLB.badge(gettext('running'), '#5cb85c')
                        : PLB.badge(ct.status || 'unknown', '#d9534f');
                    lines.push('<b>' + gettext('Container') + ':</b> CT ' + ct.vmid + ' (' +
                        Ext.htmlEncode(ct.name || '') + ') on ' + Ext.htmlEncode(ct.node) + ' ' + ctState +
                        ' ' + haState + (agentState ? ' ' + agentState : ''));
                }
                if (!ct.hastate) {
                    lines[lines.length - 1] += ' <span style="opacity:0.7">(' +
                        gettext('survive a node failure:') + ' <code>' + haHint + '</code>)</span>';
                }
                lines.push('<b>' + gettext('Cluster') + ':</b> ' + (!st.hb
                    ? PLB.badge(gettext('unknown'), '#777') + ' ' + gettext('(reported by the agent)')
                    : st.paused
                    ? PLB.badge(gettext('degraded'), '#d9534f') + ' ' +
                      gettext('real balancing paused until the cluster is healthy') + ' - ' + Ext.htmlEncode(st.paused)
                    : PLB.badge(gettext('healthy'), '#5cb85c')));

                let result;
                if (running) {
                    result = '<i class="fa fa-spinner fa-spin"></i> ' +
                        (st.dry_run ? gettext('dry-run running') : gettext('balancing running')) +
                        ' (' + gettext('started') + ' ' + PLB.ago(st.started) + ')';
                } else if (!st.finished) {
                    result = gettext('No run yet');
                    if (!st.hb) {
                        result += ' &middot; ' + gettext('the agent has not reported - on the ProxLB machine run') +
                            ' <code>proxlb-ctl check</code>' +
                            (external ? '' : ' (<code>pct exec ' + ct.vmid + ' -- proxlb-ctl check</code>)');
                    }
                } else if (st.ok) {
                    result = PLB.badge('OK', '#5cb85c') + ' ' + st.moves_total + ' ' +
                        (st.dry_run ? gettext('migration(s) planned') : gettext('migration(s) performed')) +
                        ' &middot; ' + st.guests + ' ' + gettext('guests') +
                        (st.ignored ? ', ' + st.ignored + ' ' + gettext('ignored') : '');
                } else {
                    result = PLB.badge(gettext('failed'), '#d9534f') + ' ' + Ext.htmlEncode(st.error || '');
                }
                if (st.finished && !running) {
                    result += ' &middot; ' + PLB.fmtTime(st.finished) + ' (' + PLB.ago(st.finished) + ', ' +
                        (st.dry_run ? 'dry-run' : 'balancing') + ', ' + Ext.htmlEncode(st.trigger || '') + ')';
                }
                if (queued) {
                    result += ' ' + PLB.badge(gettext('command queued'), '#337ab7');
                }
                lines.push('<b>' + gettext('Last run') + ':</b> ' + result);

                lines.push('<b>' + gettext('Automatic balancing') + ':</b> ' +
                    (auto ? PLB.badge(gettext('on'), '#5cb85c') + ' ' + gettext('ProxLB migrates guests on schedule')
                        : PLB.badge(gettext('off'), '#777') + ' ' +
                          gettext('scheduled runs only preview (dry-run)')) +
                    ' &middot; ' + gettext('next run') + ' ' + PLB.fmtTime(st.next_run));

                if (balanciness === undefined) {
                    lines.push('<b>' + gettext('Balance') + ' (' + Ext.htmlEncode(method) + '):</b> ' +
                        gettext('spread between nodes') + ' ' + spread.toFixed(1) + ' % (' +
                        gettext('threshold known after the first run') + ')');
                } else {
                    let balanced = spread <= balanciness;
                    lines.push('<b>' + gettext('Balance') + ' (' + Ext.htmlEncode(method) + '):</b> ' +
                        (balanced ? PLB.badge(gettext('balanced'), '#5cb85c')
                            : PLB.badge(gettext('imbalanced'), '#f0ad4e')) + ' ' +
                        gettext('spread between nodes') + ' ' + spread.toFixed(1) + ' % (' +
                        gettext('threshold') + ' ' + balanciness + ' %, mode ' + Ext.htmlEncode(st.mode || '-') + ')');
                }
            }
            me.summary.setHtml(lines.join('<br>'));

            // ---- buttons -----------------------------------------------------
            let canAct = !!ct && agentAlive && (external || ctRunning) && !running && !queued;
            me.btnPreview.setDisabled(!canAct);
            me.btnRun.setDisabled(!canAct || !!st.paused);
            me.btnRun.setTooltip(st.paused ? gettext('Paused: the cluster is degraded') : '');
            me.btnAuto.setDisabled(!ct);
            me.btnCt.setDisabled(!ct);
            me.btnCt.setText(external ? gettext('Open anchor VM') : gettext('Open container'));
            me.btnAuto.setIconCls(auto ? 'fa fa-toggle-on' : 'fa fa-toggle-off');
            me.btnAuto.setText(gettext('Automatic balancing') + ': ' + (auto ? gettext('on') : gettext('off')));
        },

        updateTags: function(fn, done) {
            let me = this;
            let ct = me.ct;
            if (!ct) {
                return;
            }
            let url = '/nodes/' + ct.node + '/' + ct.type + '/' + ct.vmid + '/config';
            Proxmox.Utils.API2Request({
                url: url,
                method: 'GET',
                success: function(resp) {
                    let tags = fn(PLB.splitTags(resp.result.data.tags));
                    let params = tags.length ? { tags: tags.join(';') } : { 'delete': 'tags' };
                    params.digest = resp.result.data.digest;
                    Proxmox.Utils.API2Request({
                        url: url,
                        method: 'PUT',
                        params: params,
                        waitMsgTarget: me,
                        success: function() {
                            if (done) {
                                done();
                            }
                            me.reload();
                        },
                        failure: (r) => Ext.Msg.alert(gettext('Error'), r.htmlStatus),
                    });
                },
                failure: (r) => Ext.Msg.alert(gettext('Error'), r.htmlStatus),
            });
        },

        sendCommand: function(tag) {
            let me = this;
            me.updateTags((tags) => tags.indexOf(tag) === -1 ? tags.concat([tag]) : tags);
        },

        toggleAuto: function() {
            let me = this;
            let on = me.ctTags.indexOf(PLB.TAG_ACTIVE) !== -1;
            let apply = () => me.updateTags((tags) => on
                ? tags.filter((t) => t !== PLB.TAG_ACTIVE)
                : tags.concat([PLB.TAG_ACTIVE]));
            if (on) {
                apply();
            } else {
                Ext.Msg.confirm(gettext('Confirm'),
                    gettext('Enable automatic balancing? ProxLB will then migrate guests on every scheduled run.'),
                    (btn) => { if (btn === 'yes') { apply(); } });
            }
        },
    });

    // Hook the panel into Datacenter. PVE.dc.Config builds its item list in
    // initComponent and then calls PVE.panel.Config.initComponent, so we add
    // our entry there, right after the HA section.
    Ext.define('PVE.ProxLB.ConfigOverride', {
        override: 'PVE.panel.Config',

        initComponent: function() {
            let me = this;
            if (me.$className === 'PVE.dc.Config' && Ext.isArray(me.items) &&
                !me.items.some((i) => i && i.itemId === 'proxlb')) {
                let caps = Ext.state.Manager.get('GuiCap') || {};
                if (caps.dc && caps.dc['Sys.Audit']) {
                    let entry = {
                        xtype: 'pveDcProxLBView',
                        title: 'ProxLB',
                        iconCls: 'fa fa-balance-scale',
                        itemId: 'proxlb',
                    };
                    let pos = -1;
                    me.items.forEach((item, i) => {
                        if (item && (item.itemId === 'ha' ||
                            (Ext.isArray(item.groups) && item.groups.indexOf('ha') !== -1))) {
                            pos = i;
                        }
                    });
                    if (pos >= 0) {
                        me.items.splice(pos + 1, 0, entry);
                    } else {
                        me.items.push(entry);
                    }
                }
            }
            me.callParent(arguments);
        },
    });
}());
