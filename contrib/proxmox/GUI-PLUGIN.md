# Add the ProxLB plugin to Proxmox VE and connect it to ProxLB

This guide shows how to add the **ProxLB** tab to the Proxmox VE web interface
(*Datacenter → ProxLB*) and connect it to a ProxLB instance. It covers a new
container, a ProxLB container you already have, and **ProxLB on another
machine** (a VM outside the cluster, a physical server, a Docker host).

![ProxLB tab in the Proxmox web interface](../../docs/images/proxlb-gui.png)

- [How the plugin talks to ProxLB](#how-the-plugin-talks-to-proxlb)
- [Step 1 - Add the plugin to the web interface](#step-1---add-the-plugin-to-the-web-interface)
- [Step 2 - Connect the plugin to ProxLB](#step-2---connect-the-plugin-to-proxlb)
  - [C. ProxLB on another machine](#c-proxlb-on-another-machine)
- [Step 3 - Check the connection](#step-3---check-the-connection)
- [Who may see and use the tab](#who-may-see-and-use-the-tab)
- [Troubleshooting](#troubleshooting)
- [Remove](#remove)

---

## How the plugin talks to ProxLB

The plugin needs **no extra port, URL, password or certificate**. It finds
ProxLB through the normal Proxmox API, with the session of whoever is logged in:

```
 Browser: Datacenter → ProxLB  (proxlb-gui.js)
     │  1. find the container with the tag  "proxlb"     (/cluster/resources)
     │  2. read its Notes = status written by the agent  (/nodes/<node>/lxc/<id>/config)
     │  3. buttons add a tag: proxlb-cmd-dryrun | proxlb-cmd-run | proxlb-active
     ▼
 Proxmox VE API
     ▲
     │  4. proxlb-agent polls the tags every ~10 s, runs ProxLB,
     │     writes the result into the container Notes
 LXC container (tag "proxlb")
     proxlb-agent  →  python -m proxlb -j [-d]  →  migrations through the API
```

So "connecting" the plugin to ProxLB means:

1. ProxLB runs in an **LXC container that has the tag `proxlb`**,
2. **`proxlb-agent`** runs in that container,
3. the agent's API token may **edit that container's Notes and tags**
   (`VM.Config.Options` on `/vms/<CTID>`).

`proxlb-lxc` sets all of this up for you ([Step 2A](#a-new-container-recommended)).
For a container you already have, see [Step 2B](#b-connect-an-existing-proxlb-container).
If ProxLB runs **outside** the cluster, a tiny *anchor VM* takes the container's
place as the mailbox ([Step 2C](#c-proxlb-on-another-machine)).

> If several containers have the tag `proxlb`, the plugin uses the one with the lowest ID.

---

## Step 1 - Add the plugin to the web interface

The plugin is a JavaScript file loaded by the web interface of **each node**.
Install it on every node whose web interface you open; otherwise the tab only
appears there. Pick **one** of these options.

### Option A - `.deb` package (recommended)

On **every** node, as root:

```bash
wget https://github.com/credativ/ProxLB/releases/latest/download/proxlb-pve_latest_all.deb
apt install ./proxlb-pve_latest_all.deb
```

The package enables the tab right away and **puts it back automatically after
every `pve-manager` update**. It also brings the `proxlb-lxc` and `proxlb-gui`
commands. Older versions are on the
[releases page](https://github.com/credativ/ProxLB/releases).

### Option B - script, all nodes at once

On **one** node, as root (uses the cluster's root SSH to reach the others):

```bash
git clone https://github.com/credativ/ProxLB.git
bash ProxLB/contrib/proxmox/gui/install-gui.sh --all-nodes
```

An apt hook puts the tab back after `pve-manager` updates. If you later
install the `.deb`, it takes over from the script install by itself.

### Option C - together with the container

`proxlb-lxc` (Step 2A) also installs the plugin on all nodes, unless you pass `--no-gui`.

### See the tab

1. Reload the web interface **without cache**: `Ctrl+F5` (macOS: `Cmd+Shift+R`).
2. Click **Datacenter** → **ProxLB**, just below *HA*.

Until ProxLB is connected, the tab shows **"not installed - No container with the tag proxlb found"**. That's expected: go to Step 2.

Check on a node from the shell:

```bash
grep proxlb-gui /usr/share/pve-manager/index.html.tpl      # one <script> line
ls -l /usr/share/pve-manager/js/proxlb-gui.js
```

---

## Step 2 - Connect the plugin to ProxLB

### A. New container (recommended)

On **one** node, as root:

```bash
proxlb-lxc --ip 192.168.1.50/24 --gw 192.168.1.1        # with the .deb package
# or: bash ProxLB/contrib/proxmox/proxlb-lxc.sh --ip ... # from git
```

Important options: `--storage`, `--bridge`, `--vlan`, `--interval`, `--method`,
`--balanciness`, and `--active` (balance for real at once). `--no-ha` skips
Proxmox HA. See `proxlb-lxc --help`.

This creates everything the plugin needs:

| Part | Value |
|---|---|
| Container | unprivileged Debian, tags `proxlb` and `plb_ignore_vm` (ProxLB never moves itself) |
| API user / token | `proxlb@pve!lxc<CTID>` |
| Roles | `ProxLB` on `/` (Datastore.Audit, Sys.Audit, VM.Audit, VM.Migrate), `ProxLBAgent` on `/vms/<CTID>` (VM.Config.Options) |
| Inside the CT | ProxLB in `/opt/proxlb/venv`, `proxlb-agent.service`, `/etc/proxlb/proxlb.yaml`, `/etc/proxlb/agent.yaml` |

About 30 seconds after the container starts, the agent has done its first dry-run
and the tab fills up. Continue with [Step 3](#step-3---check-the-connection).

### B. Connect an existing ProxLB container

You already run ProxLB in an LXC container, for example from the upstream
`proxlb` .deb or pip. These steps make the plugin use it. Below, `<CTID>` is the
container ID, and `proxlb@pve!mytoken` is the API token in your `proxlb.yaml`.

**1. Tag the container.** Do this on any node:

```bash
pct config <CTID> | grep ^tags                   # note any tags you already have
pct set <CTID> --tags "proxlb;plb_ignore_vm"      # replaces the list: add your old tags here too
```

`plb_ignore_vm` stops ProxLB from migrating its own container mid-run.

**2. Allow the token to write the status and read commands.** Run this on any node:

```bash
pveum role add ProxLBAgent --privs VM.Config.Options
pveum acl modify /vms/<CTID> --users proxlb@pve --roles ProxLBAgent
# only if the token has privilege separation (privsep=1):
pveum acl modify /vms/<CTID> --tokens 'proxlb@pve!mytoken' --roles ProxLBAgent
```

ProxLB itself still needs its usual rights on `/`: Datastore.Audit, Sys.Audit, VM.Audit and VM.Migrate.

**3. Install the agent in the container.** Run this on the node that runs the CT:

```bash
git clone --depth 1 https://github.com/credativ/ProxLB.git /tmp/ProxLB
pct push <CTID> /tmp/ProxLB/contrib/proxmox/agent/proxlb-agent.py /opt/proxlb-agent.py --perms 0755
pct push <CTID> /tmp/ProxLB/contrib/proxmox/agent/proxlb-ctl /usr/local/bin/proxlb-ctl --perms 0755
pct push <CTID> /tmp/ProxLB/contrib/proxmox/agent/proxlb-agent.service /etc/systemd/system/proxlb-agent.service
```

If the `.deb` is installed, the same files are under `/usr/share/proxlb-pve/src/contrib/proxmox/agent/`.

**4. Configure the agent.** Run these inside the container (`pct enter <CTID>`). First,
find the Python that has ProxLB installed:

| ProxLB installed with | Python |
|---|---|
| upstream `proxlb` .deb | `/opt/venvs/proxlb/bin/python3` |
| `proxlb-lxc` | `/opt/proxlb/venv/bin/python` |
| pip into a venv | `<venv>/bin/python` |

```bash
PY=/opt/venvs/proxlb/bin/python3          # <- your Python from the table
cat > /etc/proxlb/agent.yaml <<EOF
ctid: <CTID>
proxlb_config: /etc/proxlb/proxlb.yaml
python: $PY
poll_seconds: 10
run_timeout_seconds: 14400
state_dir: /var/lib/proxlb
pause_when_degraded: true
EOF
id plb >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/proxlb --shell /usr/sbin/nologin plb
install -d -o plb -g plb /var/lib/proxlb
chgrp plb /etc/proxlb/proxlb.yaml /etc/proxlb/agent.yaml && chmod 0640 /etc/proxlb/*.yaml

# the unit file expects the proxlb-lxc paths - point it at yours
sed -i "s#^ExecStart=.*#ExecStart=$PY /opt/proxlb-agent.py -c /etc/proxlb/agent.yaml#" \
    /etc/systemd/system/proxlb-agent.service
```

**5. Let the agent run ProxLB instead of the daemon.** Only one of them may
schedule runs:

```bash
systemctl disable --now proxlb.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now proxlb-agent.service
journalctl -u proxlb-agent -f                 # wait for "ProxLB run finished"
```

The agent takes the interval from `service.schedule` in your `proxlb.yaml`.
`service.daemon` and `service.enable_ha` are ignored. Your other settings stay as they are.

> **Safe by default:** scheduled runs are only dry-runs until you switch on
> **Automatic balancing** in the tab. If your old daemon balanced for real, switch it on there.
>
> **API host:** the agent tries every entry of `proxmox_api.hosts` in turn.
> List all nodes there, so it keeps working when one node is down.

### C. ProxLB on another machine

Use this when ProxLB runs **outside** the cluster: a VM on another hypervisor, a
physical server, your management box, or a Docker host.

![ProxLB on another machine](../../docs/images/proxlb-gui-external.png)

*The tab shows where ProxLB really runs (`mgmt01.lan`), with the anchor VM that carries the status (sample data).*

There's no ProxLB container in the cluster to hold the status, so a tiny
**anchor VM** is used instead:

```
 Proxmox web interface ──► anchor VM 9000 (tag "proxlb", Notes + tags)  ◄── HTTPS :8006 ──  proxlb-agent + ProxLB
                            no disks, no network, never started                           on mgmt01 (outside the cluster)
```

The anchor VM uses **no storage and no RAM**. It's protected against deletion
and HA-managed (state *stopped*), so it moves to a surviving node when its node fails.

**Quick way: everything in one command.** If ProxLB runs in an **LXC container of
this cluster**, or on a machine you can reach **over SSH as root**, run this on one
Proxmox VE node:

```bash
proxlb-external --ct 150                    # ProxLB runs in LXC 150 (any node)
proxlb-external --ssh root@mgmt01           # ProxLB runs on mgmt01
proxlb-external --vmid 138 --ct 150         # anchor VM 138 already exists: reuse it
proxlb-external --create-ct --ip 192.168.1.60/24 --gw 192.168.1.1
                                            # no host yet: create a new LXC for ProxLB too
```

`--create-ct` makes a new unprivileged Debian 13 (or 12) container on this node.
It has the same options as `proxlb-lxc`: `--ctid`, `--hostname`, `--storage`,
`--bridge`, `--vlan`, `--ip`, `--gw`, `--dns`, `--cores`, `--memory`, `--disk`.
It's tagged `plb_ignore_vm;proxlb-host`, never `proxlb`, because that tag marks the
anchor VM. In a cluster it's also made HA, unless you pass `--no-ha`. Without a
need for the anchor setup, `proxlb-lxc` is the simpler choice: its container
holds the status itself.

This creates (or reuses) the anchor VM and the token. It copies the ProxLB
source and the bundle to the target, runs `install-agent.sh` there, and then
deletes the bundle on both sides. With `--ct`, the container is also tagged
`plb_ignore_vm`, so ProxLB never migrates the container it runs in. For a
container on another node, it goes through that node over the cluster's root
SSH. The container must be running and have `python3` (Debian/Ubuntu/RHEL). Missing
packages such as `python3-venv` are installed automatically. Continue with step 4.

> Reusing an anchor: if its bundle `/root/proxlb-external-<VMID>.tar` is still
> there, it's used as is. Otherwise a new token is made, and an agent that used the
> old token elsewhere stops working.

**By hand, step by step:**

**1. On one Proxmox VE node** (as root), create the anchor VM, the API token and the bundle:

```bash
proxlb-external                      # with the proxlb-pve .deb
# or: bash ProxLB/contrib/proxmox/proxlb-external.sh
```

It prints the next steps and writes **`/root/proxlb-external-<VMID>.tar`** (mode 0600). The bundle contains:
- the API hosts (all cluster nodes),
- the token `proxlb@pve!external<VMID>` (role `ProxLB` on `/`, plus `ProxLBAgent` on the anchor only),
- the cluster CA, so the agent can verify TLS.

**2. Copy the bundle to the ProxLB machine.** It holds a secret, so keep it private:

```bash
scp /root/proxlb-external-9000.tar root@mgmt01:/root/
```

**3. On the ProxLB machine** (as root; Debian/Ubuntu or RHEL-like with systemd and python3):

```bash
git clone https://github.com/credativ/ProxLB.git
bash ProxLB/contrib/proxmox/agent/install-agent.sh --bundle /root/proxlb-external-9000.tar
rm /root/proxlb-external-9000.tar
```

`install-agent.sh`:
- **finds ProxLB's Python:** upstream `.deb` (`/opt/venvs/proxlb`), a `/opt/proxlb/venv`, or the system Python. If none has ProxLB, it installs ProxLB into `/opt/proxlb/venv`.
- **trusts the cluster CA** in the system store (Debian or RHEL style).
- **sets `proxmox_api` in `/etc/proxlb/proxlb.yaml`** to the bundle's token and all nodes, keeping all your other settings. A backup `proxlb.yaml.bak-<date>` is written first. Use `--keep-api-config` to leave your API settings alone; that user then needs `VM.Config.Options` on `/vms/<VMID>`.
- **writes `/etc/proxlb/agent.yaml`** (`vmid: <VMID>`, `guest_type: qemu`) and installs `proxlb-agent.service`.
- **disables `proxlb.service`**, so that only the agent schedules runs. It warns if a ProxLB **Docker** container is running: stop that yourself.

**4. Reload the web interface** (Ctrl+F5). Within ~30 seconds, *Datacenter → ProxLB*
shows **"ProxLB runs on mgmt01 - external"**.

Requirements and notes:
- **Network:** the ProxLB machine must reach every node on **TCP 8006**. Nothing has to connect *to* the ProxLB machine.
- **Node failure:** the agent tries every node in `proxmox_api.hosts`, so it keeps working when one node is down.
- **New token** (e.g. after a leak): `proxlb-external --rotate-token --vmid <VMID>`, then run `install-agent.sh --bundle ...` again. With `--ct <CTID>` or `--ssh <host>` added, it does both steps in one go.
- **Remove the agent:** `install-agent.sh --uninstall` turns `proxlb.service` back on. Then remove the anchor VM: `qm set <VMID> --protection 0 && ha-manager remove vm:<VMID> && qm destroy <VMID>`.

> **About TLS and older clusters.** Proxmox root CAs created before
> pve-cluster 9.1.2 have no *keyUsage* extension (Proxmox bug #6701). Python 3.13
> and urllib3 2.x reject such a CA in strict mode ("CA cert does not include key
> usage extension"). The agent keeps **full chain and hostname verification** and
> relaxes only that strict check, for itself and for the ProxLB runs it starts.
> To force strict mode, set `tls_relax_x509_strict: false` in `agent.yaml`. That
> works once your cluster has a new CA.

---

## Step 3 - Check the connection

Reload *Datacenter → ProxLB*. A connected setup looks like this:

| Line | Expected |
|---|---|
| **Container** | `CT <id> (proxlb) on <node>`, **running**, `HA: started` (or `HA: off`) |
| **ProxLB** *(another machine)* | `runs on <host>`, **external**, `status via anchor VM <id>`, `HA: stopped` (this is normal) |
| **Last run** | `OK - N migration(s) planned`, with a recent time |
| **Automatic balancing** | `off` (dry-run only) until you switch it on, plus the next run time |
| **Cluster** | `healthy` |
| **Balance** | `balanced` or `imbalanced`, with the spread between nodes |

Then press **Preview (dry-run)**. The status shows *command queued*, then
*dry-run running*, and within about 10-30 seconds a new *Last run* time.
**The connection works when that round trip completes.**

The container's own **Notes** (*CT → Summary*) also show a short summary,
written by the agent.

---

## Who may see and use the tab

| To ... | the user needs |
|---|---|
| see *Datacenter → ProxLB* | `Sys.Audit` on `/` (e.g. role `PVEAuditor`) |
| press *Preview*, *Rebalance now* or *Automatic balancing* | `VM.Config.Options` on `/vms/<CTID>` |

Example: a group **proxlb-operators** that may watch and operate ProxLB, but nothing else:

```bash
pveum group add proxlb-operators
pveum acl modify / --groups proxlb-operators --roles PVEAuditor
pveum role add ProxLBOperator --privs VM.Config.Options 2>/dev/null || true
pveum acl modify /vms/<CTID> --groups proxlb-operators --roles ProxLBOperator
pveum user modify alice@pve --groups proxlb-operators
```

If you restricted tags (*Datacenter → Options → Tag Style → User Allow*), also
allow the tags `proxlb-cmd-dryrun`, `proxlb-cmd-run` and `proxlb-active` for these users.

---

## Troubleshooting

**Start here:** run the self-check where the agent runs. It tests the config, the network and
TLS to every node, the token, the status guest and the permissions, and prints a fix for each problem:

```bash
pct exec <CTID> -- proxlb-ctl check        # ProxLB in a container
proxlb-ctl check                            # on the ProxLB machine itself
```

`install-agent.sh` runs the same check at the end of every installation.


| The tab shows ... | Cause / fix |
|---|---|
| **no ProxLB entry** under Datacenter | The plugin is not loaded on *this* node. Check `grep proxlb-gui /usr/share/pve-manager/index.html.tpl`, then `Ctrl+F5`. Re-apply with `proxlb-gui --reapply` (package) or `bash install-gui.sh`. You also need `Sys.Audit` on `/`. |
| **not installed - no container with the tag proxlb** | Plugin OK, ProxLB not connected: [Step 2](#step-2---connect-the-plugin-to-proxlb). Check the tag with `pct config <CTID> \| grep tags`. |
| container shown, **Last run: No run yet** for minutes | The agent isn't running or can't write the Notes. Look at `pct exec <CTID> -- journalctl -u proxlb-agent -n 50`. *"could not publish status"* means `VM.Config.Options` on `/vms/<CTID>` is missing (Step 2B.2). |
| **agent not responding** | No update for more than 15 minutes: the agent stopped or lost the API. Run `pct exec <CTID> -- systemctl status proxlb-agent` and check `proxmox_api.hosts`. |
| **Last run: failed - ...** | The message is ProxLB's own error, often API rights or an unreachable host. See `pct exec <CTID> -- proxlb-ctl last`. |
| **command queued** that never finishes | The agent doesn't pick up tags: it's stopped, or it watches another CTID (`ctid:` in `/etc/proxlb/agent.yaml`). |
| **Rebalance now** greyed out | The cluster is degraded (node offline or HA recovering), so balancing is paused on purpose. Or a run is in progress. |
| buttons give **permission denied** | The user lacks `VM.Config.Options` on `/vms/<CTID>`, or tags are restricted ([see above](#who-may-see-and-use-the-tab)). |
| agent log: **401 Unauthorized** / **API login rejected** | The token in `proxlb.yaml` isn't valid any more. This happens if `proxlb-external` ran again and replaced the token. Create a new one and install it: `proxlb-external --rotate-token --vmid <anchor> --ct <CTID>` (or `--ssh <host>`). The agent picks up the new token by itself. |
| **waiting for agent** | The guest is found but the agent never reported. Check that the agent is running. For another machine, check TCP 8006 to the nodes and the token. |
| agent log: **CA cert does not include key usage extension** | Old Proxmox CA (bug #6701) and `tls_relax_x509_strict: false`. Remove that line, or create a new cluster CA. |
| agent log: **certificate verify failed** (other reasons) | The cluster CA isn't trusted on that machine. Run `install-agent.sh --bundle` again, or copy `/etc/pve/pve-root-ca.pem` into the system CA store. |
| tab gone after a Proxmox update | Script install: the apt hook should re-apply it, so run `proxlb-gui --reapply`. The `.deb` does this through its dpkg trigger. |

Useful commands inside the container: `proxlb-ctl status | last | logs | dryrun | run | config`.

---

## Remove

```bash
apt remove proxlb-pve                                        # package, on each node
bash ProxLB/contrib/proxmox/gui/install-gui.sh --uninstall --all-nodes   # script install
```

This removes only the tab. ProxLB keeps running in its container. To remove
the container too, run `pct destroy <CTID>`, then remove the API user and roles:
`pveum user delete proxlb@pve`, `pveum role delete ProxLB`, `pveum role delete ProxLBAgent`.
