output "inventory" {
  value = {
    schema_version = 1
    experiment_id  = var.experiment_id
    region         = var.region
    vpc_id         = alicloud_vpc.experiment.id
    nodes = { for name, node in alicloud_instance.node : name => {
      id         = node.id
      private_ip = node.private_ip
      role       = var.nodes[name].role
      zone_id    = var.subnets[var.nodes[name].subnet].zone_id
    } }
    disks = { for name, disk in alicloud_ecs_disk.data : name => {
      id           = disk.id
      size_gib     = var.data_disks[name].size_gib
      initial_node = var.data_disks[name].instance_key
      purpose      = var.data_disks[name].purpose
    } }
    redis_endpoint = var.create_managed_redis ? "${alicloud_kvstore_instance.metadata[0].connection_domain}:6379" : var.existing_redis_endpoint
    nas_endpoint   = var.enable_nas ? alicloud_nas_mount_target.reference[0].mount_target_domain : null
  }
  description = "Non-secret machine/disk inventory. Save with run evidence after reviewed apply."
}
