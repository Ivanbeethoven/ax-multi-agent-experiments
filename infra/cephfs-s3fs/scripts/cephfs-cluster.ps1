# Windows PowerShell wrapper for 6-node CephFS cluster control via cluster-node-1
param(
    [Parameter(Position=0)]
    [ValidateSet("status","start","stop","restart","mount","mount-fuse","umount")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
ssh -o BatchMode=yes cluster-node-1 "/usr/local/bin/cephfs-cluster $Action"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
