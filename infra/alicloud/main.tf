locals {
  tags = merge(var.tags, { Experiment = var.experiment_id, ManagedBy = "terraform", Purpose = "agent-storage-evaluation" })
}

resource "alicloud_vpc" "experiment" {
  vpc_name   = var.experiment_id
  cidr_block = var.vpc_cidr
  tags       = local.tags
}
resource "alicloud_vswitch" "experiment" {
  for_each     = var.subnets
  vpc_id       = alicloud_vpc.experiment.id
  vswitch_name = "${var.experiment_id}-${each.key}"
  cidr_block   = each.value.cidr
  zone_id      = each.value.zone_id
}
resource "alicloud_security_group" "experiment" {
  security_group_name = var.experiment_id
  vpc_id              = alicloud_vpc.experiment.id
  description         = "Private experiment members only"
  tags                = local.tags
}
resource "alicloud_security_group_rule" "members" {
  type                     = "ingress"
  ip_protocol              = "all"
  nic_type                 = "intranet"
  policy                   = "accept"
  port_range               = "-1/-1"
  security_group_id        = alicloud_security_group.experiment.id
  source_security_group_id = alicloud_security_group.experiment.id
}
resource "alicloud_security_group_rule" "ssh" {
  for_each          = var.admin_private_cidrs
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "intranet"
  policy            = "accept"
  port_range        = "22/22"
  security_group_id = alicloud_security_group.experiment.id
  cidr_ip           = each.value
}
resource "alicloud_instance" "node" {
  for_each                   = var.nodes
  instance_name              = "${var.experiment_id}-${each.key}"
  host_name                  = each.key
  availability_zone          = var.subnets[each.value.subnet].zone_id
  vswitch_id                 = alicloud_vswitch.experiment[each.value.subnet].id
  instance_type              = each.value.instance_type
  image_id                   = var.image_id
  security_groups            = [alicloud_security_group.experiment.id]
  key_name                   = var.ssh_key_pair_name
  instance_charge_type       = "PostPaid"
  spot_strategy              = "NoSpot"
  internet_max_bandwidth_out = 0
  system_disk_category       = "cloud_essd"
  system_disk_size           = var.system_disk_gib
  user_data                  = file("${path.module}/cloud-init.yaml")
  tags                       = merge(local.tags, { Role = each.value.role })
}
resource "alicloud_ecs_disk" "data" {
  for_each             = var.data_disks
  disk_name            = "${var.experiment_id}-${each.key}"
  zone_id              = var.subnets[each.value.subnet].zone_id
  category             = "cloud_essd"
  performance_level    = "PL1"
  size                 = each.value.size_gib
  payment_type         = "PayAsYouGo"
  encrypted            = true
  enable_auto_snapshot = false
  tags = merge(local.tags, { Purpose = each.value.purpose },
  each.value.agent_id == null ? {} : { AgentID = each.value.agent_id })
}
resource "alicloud_ecs_disk_attachment" "data" {
  for_each             = { for name, disk in var.data_disks : name => disk if disk.instance_key != null }
  disk_id              = alicloud_ecs_disk.data[each.key].id
  instance_id          = alicloud_instance.node[each.value.instance_key].id
  delete_with_instance = false
  lifecycle {
    precondition {
      condition     = var.subnets[each.value.subnet].zone_id == var.subnets[var.nodes[each.value.instance_key].subnet].zone_id
      error_message = "This template uses ordinary cloud_essd disks and requires same-AZ attachments. Regional ESSD is a separate sensitivity."
    }
  }
}
