# The Azure side of the ACL-trimming demo: a resource group and one search
# service, on the FREE SKU.
#
#   terraform init
#   terraform plan      # costs nothing, needs only a logged-in `az`
#   terraform apply
#
# The free SKU is 50 MB and 3 indexes - a few thousand chunks, not the 87,173
# the published numbers describe. That is deliberate: this demonstrates
# security trimming, it does not host the corpus. Anything billable needs
# confirm_paid_tier, below.

resource "azurerm_resource_group" "this" {
  name     = "${var.prefix}-rg"
  location = var.location
  tags     = var.tags
}

resource "azurerm_search_service" "this" {
  name                = "${var.prefix}-search"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = var.sku

  # Free SKU allows exactly one of each and rejects anything else.
  replica_count   = var.sku == "free" ? 1 : var.replica_count
  partition_count = var.sku == "free" ? 1 : var.partition_count

  # Keys off by default: the store authenticates with a managed identity, and a
  # key that does not exist cannot be committed by accident.
  local_authentication_enabled = var.enable_api_keys

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags

  lifecycle {
    precondition {
      condition     = var.sku == "free" || var.confirm_paid_tier
      error_message = <<-EOT
        sku "${var.sku}" is billable - basic is roughly $75/month, and it keeps
        billing until the service is destroyed. Set confirm_paid_tier = true if
        that is intended. The free SKU (50 MB, 3 indexes) is enough for the
        ACL-trimming demo this repository actually runs.
      EOT
    }
  }
}

# Read access for whatever runs the API (a managed identity, or your own user
# while testing). Optional: no principal, no assignment.
resource "azurerm_role_assignment" "reader" {
  count                = var.reader_principal_id == "" ? 0 : 1
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = var.reader_principal_id
}

# Write access, for the one-off that builds the subset index.
resource "azurerm_role_assignment" "contributor" {
  count                = var.indexer_principal_id == "" ? 0 : 1
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = var.indexer_principal_id
}
