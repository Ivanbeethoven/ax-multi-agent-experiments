#!/usr/bin/env bash
# Cluster-wide CephFS lifecycle & mount controller (6-node idcai cluster)
# Can be run EITHER directly on jump/admin node A366 (/usr/local/bin/cephfs-cluster)
# OR from an external workstation with ssh aliases idcai-60001..idcai-60555.

set -euo pipefail

ALL_NODES=("A366" "A355" "A672" "A601" "A635" "A368")
REV_NODES=("A368" "A635" "A601" "A672" "A355" "A366")
MON_NODES=("A366" "A355" "A672")
MGR_NODES=("A366" "A672")
OSD_NODES=("A672" "A601" "A635" "A368")
MDS_NODES=("A355" "A672")

node_to_external_ssh() {
  case "$1" in
    A366) echo "idcai-60001" ;;
    A355) echo "idcai-60525" ;;
    A672) echo "idcai-60103" ;;
    A601) echo "idcai-60439" ;;
    A635) echo "idcai-60533" ;;
    A368) echo "idcai-60555" ;;
    *) echo "Unknown node $1" >&2; exit 1 ;;
  esac
}

run_on_node() {
  local node="$1"
  shift
  if [ "$(hostname)" = "A366" ]; then
    if [ "${node}" = "A366" ]; then
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
  run_on_node "A366" "for i in \$(seq 1 20); do ceph -s >/dev/null 2>&1 && break; sleep 1; done"
  for n in "${MGR_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl start-mgr"; done

  echo "=== [2/4] Attaching Loop Devices & Starting 4 OSDs (${OSD_NODES[*]}) ==="
  for n in "${OSD_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl start-osd"; done
  run_on_node "A366" "for i in \$(seq 1 30); do up=\$(ceph osd stat -f json | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"num_up_osds\",0))'); [ \"\$up\" -ge 4 ] && break; sleep 1; done; ceph osd unset noout 2>/dev/null || true"

  echo "=== [3/4] Starting CephFS MDS (active A355 first, then standby A672) ==="
  run_on_node "A355" "/usr/local/bin/cephfs-node-ctl start-mds"
  run_on_node "A366" "for i in \$(seq 1 20); do ceph fs status cephfs 2>/dev/null | grep -q 'active' && break; sleep 1; done"
  run_on_node "A672" "/usr/local/bin/cephfs-node-ctl start-mds"

  echo "=== [4/4] Mounting /mnt/cephfs across all 6 nodes ==="
  for n in "${ALL_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl mount"; done

  echo "=== Cluster Start Complete ==="
  run_on_node "A366" "ceph -s"
}

cluster_stop() {
  echo "=== [1/4] Unmounting /mnt/cephfs on all 6 nodes first (prevents hung kernel mounts) ==="
  for n in "${REV_NODES[@]}"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl umount"; done

  echo "=== [2/4] Setting OSD noout & stopping MDS (standby A672 -> active A355) ==="
  run_on_node "A366" "if systemctl is-active --quiet ceph-mon@A366.service; then timeout 5 ceph --connect-timeout 3 osd set noout 2>/dev/null || true; fi"
  run_on_node "A672" "/usr/local/bin/cephfs-node-ctl stop-mds"
  run_on_node "A355" "/usr/local/bin/cephfs-node-ctl stop-mds"

  echo "=== [3/4] Stopping 4 OSD daemons ==="
  for n in "A368" "A635" "A601" "A672"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl stop-osd"; done

  echo "=== [4/4] Stopping MGR & MON daemons ==="
  for n in "A672" "A366"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl stop-mgr"; done
  for n in "A672" "A355" "A366"; do run_on_node "${n}" "/usr/local/bin/cephfs-node-ctl stop-mon"; done

  echo "=== Cluster Stop Complete ==="
}

cluster_status() {
  echo "================ CEPH CLUSTER SUMMARY ================"
  run_on_node "A366" "if systemctl is-active --quiet ceph-mon@A366.service; then ceph -s; echo '---'; ceph fs status cephfs; else echo 'Cluster MON on A366 is currently STOPPED.'; fi"
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
