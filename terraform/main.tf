terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.14.0"
    }
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

resource "azurerm_resource_group" "az_res_gr" {
  location = var.location
  name     = var.resource_group_name
}

# Azure Virtual Network
resource "azurerm_virtual_network" "vnet_westus2" {
  location            = azurerm_resource_group.az_res_gr.location
  name                = "vnet_westus2"
  resource_group_name = azurerm_resource_group.az_res_gr.name
  address_space       = var.vnet_addr
}

# resource "azurerm_subnet" "pub_sub_westus2_lb" {
#   name                 = "pub_sub_westus2_lb"
#   resource_group_name  = azurerm_resource_group.az_res_gr.name
#   virtual_network_name = azurerm_virtual_network.vnet_westus2.name
#   address_prefixes     = var.pub_subnet
# }

resource "azurerm_subnet" "priv_sub_westus2_app" {
  name                            = "priv_sub_westus2_app"
  resource_group_name             = azurerm_resource_group.az_res_gr.name
  virtual_network_name            = azurerm_virtual_network.vnet_westus2.name
  address_prefixes                = var.priv_subnet_app
  default_outbound_access_enabled = false
}

resource "azurerm_subnet" "priv_sub_westus2_db" {
  name                                          = "priv_sub_westus2_db"
  resource_group_name                           = azurerm_resource_group.az_res_gr.name
  virtual_network_name                          = azurerm_virtual_network.vnet_westus2.name
  address_prefixes                              = var.priv_subnet_db
  default_outbound_access_enabled               = false
  private_link_service_network_policies_enabled = true

  delegation {
    name = "fs"
    service_delegation {
      name = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = [
        "Microsoft.Network/virtualNetworks/subnets/join/action",
      ]
    }
  }
}

# resource "azurerm_route_table" "pub_rt_westus2_lb" {
#   location            = azurerm_resource_group.az_res_gr.location
#   name                = "pub_rt_westus2_lb"
#   resource_group_name = azurerm_resource_group.az_res_gr.name
#
#   route {
#     name           = "external"
#     address_prefix = "0.0.0.0/0"
#     next_hop_type  = var.pub_rt_hop_type # Internet
#   }
# }

resource "azurerm_route_table" "priv_rt_westus2_app" {
  location            = azurerm_resource_group.az_res_gr.location
  name                = "priv_rt_westus2_app"
  resource_group_name = azurerm_resource_group.az_res_gr.name
}

resource "azurerm_route_table" "priv_rt_westus2_db" {
  location            = azurerm_resource_group.az_res_gr.location
  name                = "priv_rt_westus2_db"
  resource_group_name = azurerm_resource_group.az_res_gr.name
}

# resource "azurerm_subnet_route_table_association" "pub" {
#   route_table_id = azurerm_route_table.pub_rt_westus2_lb.id
#   subnet_id      = azurerm_subnet.pub_sub_westus2_lb.id
# }

resource "azurerm_subnet_route_table_association" "priv_app" {
  route_table_id = azurerm_route_table.priv_rt_westus2_app.id
  subnet_id      = azurerm_subnet.priv_sub_westus2_app.id
}

resource "azurerm_subnet_route_table_association" "priv_db" {
  route_table_id = azurerm_route_table.priv_rt_westus2_db.id
  subnet_id      = azurerm_subnet.priv_sub_westus2_db.id
}

# Add DB private DNS
resource "azurerm_private_dns_zone" "priv_dns_zone" {
  name                = var.priv_dns_zone
  resource_group_name = azurerm_resource_group.az_res_gr.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "priv_dns_zone_vnet" {
  name                = "priv_dns_zone_vnet"
  private_dns_zone_id = azurerm_private_dns_zone.priv_dns_zone.id
  virtual_network_id  = azurerm_virtual_network.vnet_westus2.id
}

# Create database
resource "azurerm_postgresql_flexible_server" "db" {
  location                      = azurerm_resource_group.az_res_gr.location
  name                          = var.db_server_name
  resource_group_name           = azurerm_resource_group.az_res_gr.name
  version                       = var.db_version
  delegated_subnet_id           = azurerm_subnet.priv_sub_westus2_db.id
  private_dns_zone_id           = azurerm_private_dns_zone.priv_dns_zone.id
  public_network_access_enabled = false
  administrator_login           = var.db_login
  administrator_password        = var.db_pass
  zone                          = "1"
  backup_retention_days         = 14
  create_mode                   = var.db_create_mode
  sku_name                      = var.db_sku_name
  storage_type                  = var.db_storage_type
  storage_mb                    = var.db_storage_mb
  storage_tier                  = var.db_storage_tier
  auto_grow_enabled             = false

  lifecycle {
    ignore_changes = [zone]
  }
  depends_on = [azurerm_private_dns_zone_virtual_network_link.priv_dns_zone_vnet]
}

resource "azurerm_postgresql_flexible_server_database" "bw" {
  name      = var.db_name
  server_id = azurerm_postgresql_flexible_server.db.id
}



# Azure LB
resource "azurerm_public_ip" "lb" {
  name                = "pub_ip_lb_westus2"
  location            = azurerm_resource_group.az_res_gr.location
  resource_group_name = azurerm_resource_group.az_res_gr.name
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_lb" "app_lb" {
  location            = azurerm_resource_group.az_res_gr.location
  name                = "app_lb"
  resource_group_name = azurerm_resource_group.az_res_gr.name
  sku                 = "Standard"
  sku_tier            = "Regional"

  frontend_ip_configuration {
    name                 = "lb_ip_conf"
    public_ip_address_id = azurerm_public_ip.lb.id
  }
}

resource "azurerm_lb_probe" "app_probe" {
  loadbalancer_id     = azurerm_lb.app_lb.id
  name                = "app_probe"
  port                = var.app_port
  protocol            = var.def_protocol
  interval_in_seconds = 5
  number_of_probes    = 2
}

resource "azurerm_lb_backend_address_pool" "lb_addr_pool" {
  loadbalancer_id = azurerm_lb.app_lb.id
  name            = "lb_address_pool"
}

resource "azurerm_lb_rule" "lb_rule" {
  loadbalancer_id                = azurerm_lb.app_lb.id
  name                           = "lb_rule"
  backend_port                   = var.app_port
  frontend_port                  = 80
  frontend_ip_configuration_name = azurerm_lb.app_lb.frontend_ip_configuration[0].name
  protocol                       = var.def_protocol
  backend_address_pool_ids       = [azurerm_lb_backend_address_pool.lb_addr_pool.id]
  load_distribution              = "Default"
  probe_id                       = azurerm_lb_probe.app_probe.id
}




# Add VM load balancer
# resource "azurerm_linux_virtual_machine" "lb" {
#   location              = azurerm_resource_group.az_res_gr.location
#   name                  = "lb"
#   resource_group_name   = azurerm_resource_group.az_res_gr.name
#   size                  = var.lb_size
#   admin_username        = var.username
#   network_interface_ids = [azurerm_network_interface.lb_net_interface.id]
#
#   admin_ssh_key {
#     username   = var.username
#     public_key = var.first_public_key
#   }
#
#   os_disk {
#     caching              = var.vm_disk_caching
#     storage_account_type = var.lb_storage_type
#   }
#
#   source_image_reference {
#     publisher = var.lb_sir_publisher
#     sku       = var.lb_sir_sku
#     version   = var.lb_sir_ver
#     offer     = var.lb_sir_offer
#   }
# }
#

#
# resource "azurerm_network_interface" "lb_net_interface" {
#   name                = "lb_network_interface"
#   location            = azurerm_resource_group.az_res_gr.location
#   resource_group_name = azurerm_resource_group.az_res_gr.name
#
#   ip_configuration {
#     name                          = "internal"
#     subnet_id                     = azurerm_subnet.pub_sub_westus2_lb.id
#     private_ip_address_allocation = var.lb_ip_alloc
#     public_ip_address_id          = azurerm_public_ip.lb.id
#   }
# }


# Add VM scale set for app deployment
resource "azurerm_linux_virtual_machine_scale_set" "app_fleet" {
  admin_username      = var.username
  location            = azurerm_resource_group.az_res_gr.location
  name                = "vmfleetwestus2"
  resource_group_name = azurerm_resource_group.az_res_gr.name
  sku                 = var.vm_cale_set_sku
  instances           = 2

  admin_ssh_key {
    username   = var.username
    public_key = var.first_public_key
  }

  source_image_reference {
    publisher = var.vm_sir_publisher
    sku       = var.vm_sir_sku
    version   = var.vm_sir_ver
    offer     = var.vm_sir_offer
  }

  os_disk {
    storage_account_type = var.vm_storage_type
    caching              = var.vm_disk_caching
    disk_size_gb         = var.vm_disk_size
  }

  scale_in {
    rule = "Default"
  }

  network_interface {
    name                      = "app_net_conf"
    primary                   = true
    network_security_group_id = azurerm_network_security_group.priv_sgr_westus2_app.id

    ip_configuration {
      name                                   = "app_ip"
      primary                                = true
      subnet_id                              = azurerm_subnet.priv_sub_westus2_app.id
      load_balancer_backend_address_pool_ids = [azurerm_lb_backend_address_pool.lb_addr_pool.id]
    }
  }
}


## Azurerm security groups
# resource "azurerm_network_security_group" "pub_sgr_westus2_lb" {
#   location            = azurerm_resource_group.az_res_gr.location
#   name                = "pub_sgr_westus2_lb"
#   resource_group_name = azurerm_resource_group.az_res_gr.name
#
#   security_rule {
#     name                       = "allow_inbound_traffic"
#     priority                   = 100
#     direction                  = "Inbound"
#     access                     = "Allow"
#     protocol                   = var.def_protocol
#     source_port_range          = "*"
#     destination_port_ranges     = ["80", "443", "8080"] # LB port. Prob will have to use 443 and 80 redirect to 443
#     source_address_prefix      = "Internet"
#     destination_address_prefix = "*"
#   }
# }

resource "azurerm_network_security_group" "priv_sgr_westus2_app" {
  location            = azurerm_resource_group.az_res_gr.location
  name                = "priv_sgr_westus2_app"
  resource_group_name = azurerm_resource_group.az_res_gr.name

  # Allow only the public subnet to reach the app port
  security_rule {
    name                       = "allow_lb_probe"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = var.def_protocol
    source_port_range          = "*"
    destination_port_range     = "5000" # app port
    source_address_prefix      = "AzureLoadBalancer"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "allow_app_from_internet"
    priority                   = 150
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "5000"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  # Block everything else from the internet
  security_rule {
    name                       = "deny_inbound_traffic"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  # Block direct outbound to the internet
  security_rule {
    name                       = "deny_outbound_traffic"
    priority                   = 100
    direction                  = "Outbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }
  # To access Vault or DB, set private endpoints
}

resource "azurerm_network_security_group" "priv_sgr_westus2_db" {
  location            = azurerm_resource_group.az_res_gr.location
  name                = "priv_sgr_westus2_db"
  resource_group_name = azurerm_resource_group.az_res_gr.name

  # Allow only the private subnet from app to reach the db port
  security_rule {
    name                       = "allow_priv_subnet_app_to_db"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = var.def_protocol
    source_port_range          = "*"
    destination_port_range     = var.db_port # db port
    source_address_prefixes    = var.priv_subnet_app
    destination_address_prefix = "VirtualNetwork"
  }

  # Block everything else from the VNet
  security_rule {
    name                       = "deny_inbound_traffic"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # Block direct outbound to the internet
  security_rule {
    name                       = "deny_outbound_traffic"
    priority                   = 100
    direction                  = "Outbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }
}

resource "azurerm_subnet_network_security_group_association" "priv_app" {
  network_security_group_id = azurerm_network_security_group.priv_sgr_westus2_app.id
  subnet_id                 = azurerm_subnet.priv_sub_westus2_app.id
}

resource "azurerm_subnet_network_security_group_association" "priv_db" {
  network_security_group_id = azurerm_network_security_group.priv_sgr_westus2_db.id
  subnet_id                 = azurerm_subnet.priv_sub_westus2_db.id
}

# resource "azurerm_subnet_network_security_group_association" "pub_lb" {
#   network_security_group_id = azurerm_network_security_group.pub_sgr_westus2_lb.id
#   subnet_id                 = azurerm_subnet.pub_sub_westus2_lb.id
# }

