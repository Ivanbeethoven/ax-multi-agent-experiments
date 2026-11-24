#!/usr/bin/env bash
# Cluster-wide CephFS lifecycle & mount controller (6-node cluster cluster)
# Can be run EITHER directly on jump/admin node node-coord (/usr/local/bin/cephfs-cluster)
# OR from an external workstation with ssh aliases cluster-node-1..cluster-node-6.

set -euo pipefail

ALL_NODES=("node-coord" "node-meta" "node-store-1" "node-store-2" "node-store-3" "node-store-4")
REV_NODES=("node-store-4" "node-store-3" "node-store-2" "node-store-1" "node-meta" "node-coord")
MON_NODES=("node-coord" "node-meta" "node-store-1")
MGR_NODES=("node-coord" "node-store-1")
OSD_NODES=("node-store-1" "node-store-2" "node-store-3" "node-store-4")
MDS_NODES=("node-meta" "node-store-1")

node_to_external_ssh() {
  case "$1" in
    node-coord) echo "cluster-node-1" ;;
    node-meta) echo "cluster-node-2" ;;
    node-store-1) echo "cluster-node-3" ;;
    node-store-2) echo "cluster-node-4" ;;
    node-store-3) echo "cluster-node-5" ;;
    node-store-4) echo "cluster-node-6" ;;
    *) echo "Unknown node $1" >&2; exit 1 ;;
  esac
}

run_on_node() {
  local node="$1"
  shift
  if [ "$(hostname)" = "node-coord" ]; then
    if [ "${node}" = "node-coord" ]; then
      bash -c "$*"
    else
      ssh -F /etc/ceph/ops_ssh/ssh_config -o BatchMode=yes "${node}" "$*"
    fi
  else
    local alias_name
    alias_name="$(node_to_external_ssh "${node}")"
    ssh -o BatchMode=yes "${alias_name}" "$*"
  fi
}

cluster_start() {
  echo "=== [1/4] Starting MON quorum (${MON_NODES[*]}) & MGR (${MGR_NODES[*]}) ==="
  for n in "${MON_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl start-mon"; done
  run_on_node "node-coord" "for i in \$(seq 1 20); do ceph -s >/dev/null 2>&1 && break; sleep 1; done"
  for n in "${MGR_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl start-mgr"; done

  echo "=== [2/4] Attaching Loop Devices & Starting 4 OSDs (${OSD_NODES[*]}) ==="
  for n in "${OSD_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl start-osd"; done
  run_on_node "node-coord" "for i in \$(seq 1 30); do up=\$(ceph osd stat -f json | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"num_up_osds\",0))'); [ \"\$up\" -ge 4 ] && break; sleep 1; done; ceph osd unset noout 2>/dev/null || true"

  echo "=== [3/4] Starting CephFS MDS (active node-meta first, then standby node-store-1) ==="
  run_on_node "node-meta" "/usr/local/bin/cephfs-node-ctl start-mds"
  run_on_node "node-coord" "for i in \$(seq 1 20); do ceph fs status cephfs 2>/dev/null | grep -q 'active' && break; sleep 1; done"
  run_on_node "node-store-1" "/usr/local/bin/cephfs-node-ctl start-mds"

  echo "=== [4/4] Mounting /mnt/cephfs across all 6 nodes ==="
  for n in "${ALL_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl mount"; done

  echo "=== Cluster Start Complete ==="
  run_on_node "node-coord" "ceph -s"
}

cluster_stop() {
  echo "=== [1/4] Unmounting /mnt/cephfs on all 6 nodes first (prevents hung kernel mounts) ==="
  for n in "${REV_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl umount"; done

  echo "=== [2/4] Setting OSD noout & stopping MDS (standby node-store-1 -> active node-meta) ==="
  run_on_node "node-coord" "if systemctl is-active --quiet ceph-mon@node-coord.service; then timeout 5 ceph --connect-timeout 3 osd set noout 2>/dev/null || true; fi"
  run_on_node "node-store-1" "/usr/local/bin/cephfs-node-ctl stop-mds"
  run_on_node "node-meta" "/usr/local/bin/cephfs-node-ctl stop-mds"

  echo "=== [3/4] Stopping 4 OSD daemons ==="
  for n in "node-store-4" "node-store-3" "node-store-2" "node-store-1"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl stop-osd"; done

  echo "=== [4/4] Stopping MGR & MON daemons ==="
  for n in "node-store-1" "node-coord"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl stop-mgr"; done
  for n in "node-store-1" "node-meta" "node-coord"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl stop-mon"; done

  echo "=== Cluster Stop Complete ==="
}

cluster_status() {
  echo "================ CEPH CLUSTER SUMMARY ================"
  run_on_node "node-coord" "if systemctl is-active --quiet ceph-mon@node-coord.service; then ceph -s; echo '---'; ceph fs status cephfs; else echo 'Cluster MON on node-coord is currently STOPPED.'; fi"
  echo "================ PER-NODE STATUS (6 NODES) ================"
  for n in "${ALL_NODES[@]}"; do
    run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl status"
  done
}

case "${1:-status}" in
  start)       cluster_start ;;
  stop)        cluster_stop ;;
  restart)     cluster_stop; sleep 2; cluster_start ;;
  status)      cluster_status ;;
  mount)       for n in "${ALL_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl mount"; done ;;
  mount-fuse)  for n in "${ALL_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl mount-fuse"; done ;;
  umount)      for n in "${REV_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl umount"; done ;;
  *)
    echo "Usage: $0 {status|start|stop|restart|mount|mount-fuse|umount}" >&2
    exit 1
    ;;
esac
