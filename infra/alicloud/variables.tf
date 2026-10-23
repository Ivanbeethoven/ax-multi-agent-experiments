variable "region" {
  type        = string
  description = "Explicit Alibaba Cloud region; no inferred account default."
}
variable "experiment_id" {
  type        = string
  description = "Unique name used in tags, resource names and evidence."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.experiment_id))
    error_message = "Use 3-25 lowercase letters, digits and hyphens, starting with a letter."
  }
}
variable "vpc_cidr" {
  type    = string
  default = "10.72.0.0/16"
}
variable "subnets" {
  type        = map(object({ zone_id = string, cidr = string }))
  description = "Subnet key to AZ/CIDR. Use one AZ for the ordinary ESSD main comparison."
}
variable "image_id" {
  type        = string
  description = "A pinned Linux image ID with required tools preloaded; no latest-image selection."
}
variable "ssh_key_pair_name" {
  type        = string
  description = "Existing ECS SSH key pair name; no private key is handled by this module."
}
variable "admin_private_cidrs" {
  type        = set(string)
  default     = []
  description = "Optional SSH sources reachable through an existing private VPN/bastion route."
}
variable "nodes" {
  type = map(object({
    role          = string
    instance_type = string
    subnet        = string
  }))
  description = "Separate client, control, s3 and ceph nodes; explicit sizes/counts avoid automatic spending."
  validation {
    condition     = alltrue([for node in values(var.nodes) : contains(["client", "control", "s3", "ceph"], node.role)])
    error_message = "Node role must be client, control, s3 or ceph."
  }
}
variable "system_disk_gib" {
  type    = number
  default = 40
}
variable "data_disks" {
  type = map(object({
    subnet       = string
    size_gib     = number
    instance_key = optional(string)
    purpose      = string
    agent_id     = optional(string)
  }))
  default     = {}
  description = "Separate PayAsYouGo ESSD PL1 disks. instance_key null leaves a disk unattached."
  validation {
    condition     = alltrue([for disk in values(var.data_disks) : disk.size_gib >= 20 && disk.size_gib <= 65536])
    error_message = "ESSD PL1 capacity must be 20-65536 GiB."
  }
}
variable "create_managed_redis" {
  type    = bool
  default = false
}
variable "existing_redis_endpoint" {
  type        = string
  default     = null
  nullable    = true
  description = "Private host:port only; never include credentials. Record its resources and policy separately."
}
variable "redis_subnet" {
  type     = string
  default  = null
  nullable = true
}
variable "redis_instance_class" {
  type        = string
  default     = null
  nullable    = true
  description = "Explicit available class checked for the chosen region/AZ and engine."
}
variable "redis_engine_version" {
  type    = string
  default = "7.0"
}
variable "redis_password" {
  type        = string
  default     = null
  nullable    = true
  sensitive   = true
  description = "Set only by TF_VAR_redis_password when creating Redis. Terraform state contains this value."
}
variable "enable_nas" {
  type    = bool
  default = false
}
variable "nas_subnet" {
  type     = string
  default  = null
  nullable = true
}
variable "nas_storage_type" {
  type    = string
  default = "Performance"
  validation {
    condition     = contains(["Capacity", "Performance", "Premium"], var.nas_storage_type)
    error_message = "Choose a General-purpose NAS storage type."
  }
}
variable "tags" {
  type    = map(string)
  default = {}
}
