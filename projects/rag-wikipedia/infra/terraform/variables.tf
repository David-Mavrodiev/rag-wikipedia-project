variable "prefix" {
  description = "Name prefix for every resource. Must be globally unique for the search service."
  type        = string
  default     = "rag-wikipedia"

  validation {
    condition     = can(regex("^[a-z0-9-]{2,40}$", var.prefix))
    error_message = "prefix must be lower-case letters, digits and hyphens (Azure search names are strict)."
  }
}

variable "location" {
  description = "Azure region. An EU region keeps the demo inside the EU."
  type        = string
  default     = "francecentral"
}

variable "sku" {
  description = "Search service SKU. `free` is 50 MB and 3 indexes, and costs nothing."
  type        = string
  default     = "free"

  validation {
    condition     = contains(["free", "basic", "standard"], var.sku)
    error_message = "sku must be one of: free, basic, standard."
  }
}

variable "confirm_paid_tier" {
  description = <<-EOT
    Required to be true for any SKU other than `free`. A deliberate speed bump:
    basic is about $75/month and keeps billing until destroyed.
  EOT
  type        = bool
  default     = false
}

variable "replica_count" {
  description = "Ignored on the free SKU, which allows exactly one."
  type        = number
  default     = 1
}

variable "partition_count" {
  description = "Ignored on the free SKU, which allows exactly one."
  type        = number
  default     = 1
}

variable "enable_api_keys" {
  description = <<-EOT
    Local (key) authentication. Off by default: the store uses a managed
    identity, and a key that does not exist cannot be committed by accident.
  EOT
  type        = bool
  default     = false
}

variable "reader_principal_id" {
  description = "Object id that may READ the index (the API's identity). Empty: no assignment."
  type        = string
  default     = ""
}

variable "indexer_principal_id" {
  description = "Object id that may WRITE the index (whoever builds the subset). Empty: none."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags on every resource, so a stray demo is recognisable in a bill."
  type        = map(string)
  default = {
    project = "rag-wikipedia"
    purpose = "acl-trimming-demo"
    cost    = "free-tier"
  }
}
