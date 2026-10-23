# Managed Redis is optional; RustFS and CephFS are NOT provisioned as managed services.
resource "alicloud_kvstore_instance" "metadata" {
  count            = var.create_managed_redis ? 1 : 0
  db_instance_name = "${var.experiment_id}-redis"
  instance_type    = "Redis"
  engine_version   = var.redis_engine_version
  instance_class   = var.redis_instance_class
  payment_type     = "PostPaid"
  zone_id          = var.subnets[var.redis_subnet].zone_id
  vswitch_id       = alicloud_vswitch.experiment[var.redis_subnet].id
  password         = var.redis_password
  security_ips     = [var.vpc_cidr]
  config = {
    appendonly     = "yes"
    EvictionPolicy = "noeviction"
  }
  tags = local.tags
  lifecycle {
    precondition {
      condition     = var.existing_redis_endpoint == null && var.redis_instance_class != null && var.redis_password != null
      error_message = "Creating Redis requires an explicit class/password and no existing endpoint."
    }
  }
}

# Optional B5 managed NAS/NFS. This is not CephFS.
resource "alicloud_nas_file_system" "reference" {
  count            = var.enable_nas ? 1 : 0
  protocol_type    = "NFS"
  storage_type     = var.nas_storage_type
  file_system_type = "standard"
  encrypt_type     = 1
  description      = "${var.experiment_id}-nas"
  zone_id          = var.subnets[var.nas_subnet].zone_id
  tags             = local.tags
}
resource "alicloud_nas_access_group" "reference" {
  count             = var.enable_nas ? 1 : 0
  access_group_name = "${var.experiment_id}-nas"
  access_group_type = "Vpc"
  file_system_type  = "standard"
}
resource "alicloud_nas_access_rule" "reference" {
  count             = var.enable_nas ? 1 : 0
  access_group_name = alicloud_nas_access_group.reference[0].access_group_name
  file_system_type  = "standard"
  source_cidr_ip    = var.vpc_cidr
  rw_access_type    = "RDWR"
  user_access_type  = "no_squash"
  priority          = 1
}
resource "alicloud_nas_mount_target" "reference" {
  count             = var.enable_nas ? 1 : 0
  file_system_id    = alicloud_nas_file_system.reference[0].id
  access_group_name = alicloud_nas_access_group.reference[0].access_group_name
  network_type      = "Vpc"
  vpc_id            = alicloud_vpc.experiment.id
  vswitch_id        = alicloud_vswitch.experiment[var.nas_subnet].id
}
