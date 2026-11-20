// Unit tests for the ProxLB GUI plugin logic with a stubbed ExtJS / PVE.
// Run: node --test contrib/proxmox/gui/tests/plugin.test.js
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadPlugin() {
    const classes = {};
    const calls = [];
    const ctx = {
        gettext: (s) => s,
        Ext: {
            define: (name, cfg) => { classes[name] = cfg; },
            htmlEncode: (s) => String(s).replace(/</g, '&lt;'),
            isArray: Array.isArray,
            String: { format: (f, ...a) => f.replace(/\{(\d)\}/g, (m, i) => a[i]) },
            Date: { format: (d) => d.toISOString().slice(0, 16) },
            state: { Manager: { get: () => ({ dc: { 'Sys.Audit': 1 } }) } },
        },
        PVE: { panel: {}, dc: {} },
        Proxmox: { Utils: { API2Request: (o) => calls.push(o) } },
        Date, JSON, Math, setInterval, clearInterval,
    };
    vm.createContext(ctx);
    const src = fs.readFileSync(path.join(__dirname, '..', 'proxlb-gui.js'), 'utf8');
    vm.runInContext(src, ctx);
    return { ctx, classes, calls };
}

const now = () => Math.floor(Date.now() / 1000);
function statusJson(extra) {
    return Object.assign({
        state: 'idle', dry_run: true, ok: true, finished: now() - 60, started: now() - 80,
        trigger: 'gui', moves_total: 1, moves: [[101, 'web<1>', 'vm', 'pve1', 'pve2']],
        guests: 3, ignored: 1, method: 'memory', mode: 'used', balanciness: 5,
        nodes: { pve1: [80, 20, 10, 0], pve2: [30, 10, 10, 0] }, hb: now(), next_run: now() + 3600,
    }, extra || {});
}

function makeView(classes, overrides) {
    const mkStore = () => ({ data: null, loadData(d) { this.data = d; } });
    const mkBtn = () => ({
        setDisabled(v) { this.disabled = v; }, setIconCls() {}, setTooltip() {}, setText(t) { this.text = t; },
    });
    const me = Object.assign(Object.create(classes['PVE.dc.ProxLBView']), {
        nodeStore: mkStore(), moveStore: mkStore(), summary: { setHtml(h) { this.html = h; } },
        btnPreview: mkBtn(), btnRun: mkBtn(), btnAuto: mkBtn(), btnCt: mkBtn(),
        status: statusJson(),
        ct: { vmid: 120, node: 'pve1', status: 'running', name: 'proxlb', hastate: 'started' },
        ctTags: ['proxlb'],
        resources: [
            { type: 'node', node: 'pve1', status: 'online', cpu: 0.2, maxcpu: 8, mem: 8, maxmem: 10, disk: 1, maxdisk: 10 },
            { type: 'node', node: 'pve2', status: 'online', cpu: 0.1, maxcpu: 8, mem: 3, maxmem: 10, disk: 1, maxdisk: 10 },
            { type: 'qemu', node: 'pve1', status: 'running' },
        ],
    }, overrides || {});
    me.down = () => ({ setTitle(t) { me.movesTitle = t; } });
    return me;
}
const text = (html) => html.replace(/<[^>]+>/g, '');

test('parseStatus reads the agent JSON from the container notes', () => {
    const { ctx } = loadPlugin();
    const PLB = ctx.PVE.ProxLB;
    const desc = 'notes\n<!--proxlb-status:' + JSON.stringify(statusJson()) + '-->';
    assert.strictEqual(PLB.parseStatus(desc).moves_total, 1);
    assert.strictEqual(PLB.parseStatus('no marker'), null);
    assert.strictEqual(PLB.parseStatus('<!--proxlb-status:{broken-->'), null);
    assert.deepStrictEqual(Array.from(PLB.splitTags('a;b, c')), ['a', 'b', 'c']);
});

test('render shows nodes, moves and a healthy HA cluster', () => {
    const { classes } = loadPlugin();
    const me = makeView(classes);
    me.render_();
    assert.strictEqual(me.nodeStore.data.length, 2);
    assert.strictEqual(me.nodeStore.data[0].mem, 80);
    assert.strictEqual(me.nodeStore.data[0].out, 1);
    assert.strictEqual(me.nodeStore.data[1].in, 1);
    assert.strictEqual(me.moveStore.data[0].name, 'web<1>');
    assert.match(me.movesTitle, /Planned migrations/);
    const s = text(me.summary.html);
    assert.match(s, /HA: started/);
    assert.match(s, /Cluster: healthy/);
    assert.match(s, /imbalanced/); // 80 % vs 30 % > balanciness 5
    assert.strictEqual(me.btnRun.disabled, false);
});

test('degraded cluster disables Rebalance now', () => {
    const { classes } = loadPlugin();
    const me = makeView(classes, { status: statusJson({ paused: 'node(s) offline: pve3' }) });
    me.render_();
    assert.match(text(me.summary.html), /degraded.*node\(s\) offline: pve3/);
    assert.strictEqual(me.btnRun.disabled, true);
    assert.strictEqual(me.btnPreview.disabled, false);
});

test('queued command and missing container disable the buttons', () => {
    const { classes } = loadPlugin();
    const me = makeView(classes, { ctTags: ['proxlb', 'proxlb-cmd-run'] });
    me.render_();
    assert.strictEqual(me.btnPreview.disabled, true);
    const none = makeView(classes, { ct: null, status: null, ctTags: [] });
    none.render_();
    assert.match(text(none.summary.html), /not installed/);
    assert.strictEqual(none.btnRun.disabled, true);
});

test('Datacenter entry is inserted after the HA section, once', () => {
    const { classes } = loadPlugin();
    const ov = classes['PVE.ProxLB.ConfigOverride'];
    const cfg = {
        $className: 'PVE.dc.Config', callParent() {},
        items: [{ itemId: 'summary' }, { itemId: 'ha' }, { itemId: 'ha-groups', groups: ['ha'] }, { itemId: 'acme' }],
    };
    ov.initComponent.call(cfg);
    ov.initComponent.call(cfg);
    assert.deepStrictEqual(cfg.items.map((i) => i.itemId), ['summary', 'ha', 'ha-groups', 'proxlb', 'acme']);
});

test('buttons send commands as container tags', () => {
    const { classes, calls } = loadPlugin();
    const me = makeView(classes);
    me.reload = () => {};
    me.sendCommand('proxlb-cmd-dryrun');
    calls[0].success({ result: { data: { tags: 'proxlb;plb_ignore_vm', digest: 'd1' } } });
    assert.strictEqual(calls[1].method, 'PUT');
    assert.strictEqual(calls[1].params.tags, 'proxlb;plb_ignore_vm;proxlb-cmd-dryrun');
    assert.strictEqual(calls[1].params.digest, 'd1');
});

test('ProxLB on another machine: anchor VM (stopped) with a live agent', () => {
    const { classes, calls } = loadPlugin();
    const anchor = { type: 'qemu', vmid: 9000, node: 'pve2', status: 'stopped', name: 'proxlb-anchor', hastate: 'stopped' };
    const me = makeView(classes, { ct: anchor, status: statusJson({ host: 'mgmt01', guest_type: 'qemu' }) });
    me.render_();
    const s = text(me.summary.html);
    assert.match(s, /runs on mgmt01/);
    assert.match(s, /external/);
    assert.match(s, /anchor VM 9000/);
    assert.match(s, /HA: stopped/);
    assert.doesNotMatch(s, /agent not responding|waiting for agent/);
    assert.strictEqual(me.btnPreview.disabled, false); // stopped VM is fine, agent is alive
    assert.strictEqual(me.btnCt.text, 'Open anchor VM');

    me.reload = () => {};
    me.sendCommand('proxlb-cmd-run');
    assert.strictEqual(calls[0].url, '/nodes/pve2/qemu/9000/config');
});

test('buttons need a live agent', () => {
    const { classes } = loadPlugin();
    const anchor = { type: 'qemu', vmid: 9000, node: 'pve2', status: 'stopped', name: 'a' };
    const stale = makeView(classes, { ct: anchor, status: statusJson({ host: 'mgmt01', hb: now() - 3600 }) });
    stale.render_();
    assert.match(text(stale.summary.html), /agent not responding/);
    assert.match(text(stale.summary.html), /ha-manager add vm:9000/);
    assert.strictEqual(stale.btnPreview.disabled, true);

    const fresh = makeView(classes, { status: {} }); // container running, agent never reported
    fresh.render_();
    assert.match(text(fresh.summary.html), /waiting for agent/);
    assert.strictEqual(fresh.btnPreview.disabled, true);
});

test('no run yet: no fake verdict, threshold or "balanced" claim', () => {
    const { classes } = loadPlugin();
    const anchor = { type: 'qemu', vmid: 1000, node: 'node04', status: 'stopped', name: 'proxlb-anchor', hastate: 'stopped' };
    const me = makeView(classes, { ct: anchor, status: null, ctTags: ['proxlb', 'proxlb-active'] });
    me.status = {};
    let refreshed = false;
    me.down = () => ({ setTitle(t) { me.movesTitle = t; }, getView: () => ({ refresh() { refreshed = true; } }) });
    me.render_();
    const s = text(me.summary.html);
    assert.match(s, /waiting for agent/);
    assert.match(s, /No run yet .* proxlb-ctl check/);
    assert.match(s, /Cluster: unknown/);
    assert.match(s, /threshold known after the first run/);
    assert.doesNotMatch(s, /imbalanced|threshold 10/);
    assert.strictEqual(me.movesTitle, 'Migrations of the last run - no run yet');
    // view.refresh() before the grid has a size blanks the whole tab on ExtJS
    assert.strictEqual(refreshed, false);
});

test('plugin never refreshes grid views itself (blank-tab bug)', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'proxlb-gui.js'), 'utf8');
    assert.ok(!/\.refresh\(\)/.test(src), 'view.refresh() found in proxlb-gui.js');
});

test('plugin source is not strict mode (ExtJS callParent needs fn.caller)', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'proxlb-gui.js'), 'utf8');
    assert.ok(!/^\s*['"]use strict['"]/m.test(src));
});
