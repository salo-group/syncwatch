# SPDX-License-Identifier: Apache-2.0
# Automake's Windows default assumes static contrib libraries.
# Keep compiler support static and link the owned codec DLLs.
vlc_static_plugin_flag := -Wc,-static
ifneq ($(words $(filter $(vlc_static_plugin_flag),$(AM_LDFLAGS))),1)
$(error Review the upstream Windows plugin link flags)
endif
override AM_LDFLAGS := $(filter-out $(vlc_static_plugin_flag),$(AM_LDFLAGS)) -Wc,-static-libgcc -Wc,-static-libstdc++
