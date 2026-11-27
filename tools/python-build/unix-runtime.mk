# Keep the bootstrap's source location relative to the configured build.
VPATH := ../cpython

ifeq ($(MACHDEP),darwin)
# The original dylib link rule does not include the selected static modules.
SHLIBS += $(MODLIBS) -Wl,-install_name,@rpath/libpython$(LDVERSION).dylib
endif
