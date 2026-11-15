"""Validate one physical service node against a declared experiment pool."""
import ipaddress
import re


def private_ipv4(value):
    address=ipaddress.IPv4Address(value)
    if not any(address in ipaddress.IPv4Network(network) for network in
               ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
        raise ValueError('A private VPC IPv4 address is required')
    return str(address)


def bind_service(spec,inventory,pool_id,role):
    if not re.fullmatch(r'pool-[a-z0-9][a-z0-9-]{0,20}',pool_id):
        raise ValueError('Explicit pool ID required')
    if (spec.get('created_for_campaign') is not True or spec.get('role')!=role or
            not re.fullmatch(r'ax-pilot-[0-9]{8}(-[a-z0-9]+)?',spec.get('campaign','')) or
            not spec.get('instance_id','').startswith('i-') or not spec.get('hostname')):
        raise ValueError('Owned service-node spec required')
    pools=inventory.get('pools')
    if pools is None:pools=[inventory]
    if not isinstance(pools,list) or not all(isinstance(p,dict) for p in pools) or len({p.get('pool_id') for p in pools})!=len(pools):
        raise ValueError('Unique physical pools required')
    matches=[p for p in pools if p.get('pool_id')==pool_id]
    if len(matches)!=1:raise ValueError('Service pool is absent from inventory')
    node=matches[0].get('roles',{}).get(role)
    if not isinstance(node,dict):raise ValueError('Service role is absent from pool')
    if node.get('instance_id')!=spec['instance_id'] or node.get('hostname')!=spec['hostname']:
        raise ValueError('Inventory and node identity differ')
    actual=[disk['id'] for disk in spec.get('data_disks',[])]
    declared=node.get('data_disk_ids',[])
    if not actual or not all(isinstance(d,str) and d.startswith('d-') for d in actual+declared) or len(actual)!=len(set(actual)) or len(declared)!=len(set(declared)) or set(actual)!=set(declared):
        raise ValueError('Inventory and node data-disk identities differ')
    address=private_ipv4(node['private_ip'])
    if spec.get('private_ip',address)!=address:
        raise ValueError('Spec and inventory private IP differ')
    cluster=spec['campaign']+'-'+pool_id
    project=cluster+'-objects-01'
    if not re.fullmatch(r'ax-[a-z0-9-]{3,55}',cluster) or not re.fullmatch(r'ax-[a-z0-9-]{3,55}',project):
        raise ValueError('Campaign/pool service names exceed deployment limits')
    return dict(pool_id=pool_id,campaign=spec['campaign'],role=role,
                instance_id=spec['instance_id'],hostname=spec['hostname'],private_ip=address,
                data_disk_ids=actual,cluster_id=cluster,project_name=project)


def require_local_address(address,interfaces):
    address=private_ipv4(address)
    local={row['local'] for interface in interfaces for row in interface.get('addr_info',[])
           if row.get('family')=='inet' and 'local' in row}
    if address not in local:raise ValueError('Declared service IP is not assigned to this host')
    return address


def legacy_objects_binding():
    return dict(pool_id='pool-a',campaign='ax-pilot-20261111',role='objects',
                hostname='ax-pilot-20261111-objects',private_ip='10.20.2.163',
                cluster_id='ax-pilot-20261111-pool-a',project_name='ax-pilot-20261111-objects-01')


def objects_deployment_spec(binding,image,mounts,credentials):
    return dict(mode='single-node-four-disk',image=image,
        observability=dict(cluster_id=binding['cluster_id'],meter_interval_seconds=1),
        experiment=dict(project_name=binding['project_name'],protocol_profile='unified-pilot-20261111',
            stage='C1-C4',cpus=6,memory_bytes=24*1024**3,layout_profile='enabled'),
        nodes=[dict(private_ip=binding['private_ip'],data_mounts=list(map(str,mounts)),
            log_dir='/srv/ax-objects-0/rustfs-logs',credentials_file=str(credentials))])
