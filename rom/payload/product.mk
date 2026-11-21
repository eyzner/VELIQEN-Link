# SPDX-License-Identifier: GPL-3.0-or-later
# Include once from the selected product makefile.
PRODUCT_PACKAGES += VELIQENLink

PRODUCT_COPY_FILES += \
    vendor/veliqen/link/permissions/privapp-permissions-veliqen-link.xml:$(TARGET_COPY_OUT_PRODUCT)/etc/permissions/privapp-permissions-veliqen-link.xml
