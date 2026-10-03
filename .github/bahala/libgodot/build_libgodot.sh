#!/bin/bash

set -eux

BASE_DIR="$( cd "$(dirname "$0")" ; pwd -P )"

GODOT_DIR="$BASE_DIR/godot"
GODOT_CPP_DIR="$BASE_DIR/godot-cpp"
SWIFT_GODOT_DIR="$BASE_DIR/SwiftGodot"
SWIFT_GODOT_KIT_DIR="$BASE_DIR/SwiftGodotKit"
BUILD_DIR=$BASE_DIR/build
BUILD_GDEXTENSION_DIR="$BUILD_DIR/gdextension"

host_system="$(uname -s)"
host_arch="$(uname -m)"
host_target="editor"
target="editor"
target_arch=""
host_build_options=""
target_build_options=""
lib_suffix="so"
host_debug=1
debug=1
dev_build=0
force_host_rebuild=0
update_api=0
simulator=0
library_type="auto"
profiler_type=""

angle_libs="$BASE_DIR/ANGLE"

case "$host_system" in
    Linux)
        host_platform="linuxbsd"
        cpus="$(nproc)"
        target_platform="linuxbsd"
    ;;
    Darwin)
        host_platform="macos"
        cpus="$(sysctl -n hw.logicalcpu)"
        target_platform="macos"
        lib_suffix="dylib"
    ;;
    *)
        echo "System $host_system is unsupported"
        exit 1
    ;;
esac


while [ "${1:-}" != "" ]
do
    case "$1" in
        --host-rebuild)
            force_host_rebuild=1
        ;;
        --host-debug)
            host_debug=1
        ;;        
        --host-release)
            host_debug=0
        ;;
        --update-api)
            update_api=1
            force_host_rebuild=1
        ;;
        --debug)
            debug=1
        ;;
        --dev)
            debug=1
            dev_build=1
        ;;
        --release)
            debug=0
        ;;
        --target)
            shift
            target_platform="${1:-}"
        ;;
        --no-target)
            target_platform=""
        ;;
        --simulator)
            simulator=1
        ;;
        --target-arch)
            shift
            target_arch="${1:-}"
        ;;
        --library-type)
            shift
            library_type="${1:-}"
        ;;
        --profiler-type)
            shift
            profiler_type="${1:-}"
        ;;
        *)
            echo "Usage: $0 [--host-debug] [--host-rebuild] [--host-debug] [--host-release] [--debug] [--release] [--update-api] [--target <target platform>] [--target-arch <target platform>] [--profiler-type <profiler type>]"
            exit 1
        ;;
    esac
    shift
done

target_build_options="$target_build_options opengl3=yes"

if [ "$target_platform" = "ios" ]
then
    target="template_release"
    target_build_options="$target_build_options vulkan=no metal=yes library_type=static_library"
    lib_suffix="a"
    if [ $simulator -eq 1 ]
    then
        target_build_options="$target_build_options ios_simulator=true"
    fi
    if [ "$target_arch" = "" ]
    then
        target_arch="arm64"
    fi
fi

if [ "$target_platform" = "android" ]
then
    target_build_options="$target_build_options vulkan=yes angle_libs=$angle_libs"
    target="template_release"
    if [ "$library_type" = "auto" ]
    then
        library_type="shared_library"
    fi
    if [ "$target_arch" = "" ]
    then
        target_arch="arm64"
    fi
    if [ "$library_type" = "static_library" ]
    then
        lib_suffix="a"
        target_build_options="$target_build_options library_type=static_library"
    else
        lib_suffix="so"
        target_build_options="$target_build_options library_type=shared_library"
    fi
fi

if [ "$target_arch" = "" ]
then
    target_arch="$host_arch"
fi

host_godot_suffix="$host_platform.$host_target"

if [ $host_debug -eq 1 ]
then
    host_build_options="$host_build_options dev_build=yes"
    host_godot_suffix="$host_godot_suffix.dev"
else
    host_build_options="$host_build_options" 
    # host_build_options="$host_build_options production=yes optimize=size lto=full"
fi

host_godot_suffix="$host_godot_suffix.$host_arch"

target_godot_suffix="$target_platform.$target"

if [ $debug -eq 1 ]
then
    if [ "$target_platform" = "ios" ] || [ "$target_platform" = "android" ] 
    then
        target="template_debug"
        target_godot_suffix="$target_platform.$target"
    fi
fi

if [ $dev_build -eq 1 ]
then
    target_build_options="$target_build_options dev_build=yes lto=none"
    target_godot_suffix="$target_godot_suffix.dev"
    angle_libs="$angle_libs/debug"
else
    angle_libs="$angle_libs/release"
fi

if [ "$profiler_type" = "tracy" ]
then
    host_build_options="$host_build_options profiler=tracy"
    target_build_options="$target_build_options profiler=tracy"
elif [ "$profiler_type" = "perfetto" ]
then
    host_build_options="$host_build_options profiler=perfetto"
    target_build_options="$target_build_options profiler=perfetto"
fi

if [ $dev_build -eq 0 ] && [ $debug -eq 0 ]
then
    target_build_options="$target_build_options production=yes"
    # Release build
    if [ "$target_platform" = "android" ]
    then
        target_build_options="$target_build_options debug_symbols=yes optimize=speed_trace lto=none"
    else
        target_build_options="$target_build_options optimize=speed_trace lto=full"
    fi
fi

target_godot_suffix="$target_godot_suffix.$target_arch"

host_godot="$GODOT_DIR/bin/godot.$host_godot_suffix"
target_godot="$GODOT_DIR/bin/libgodot.$target_godot_suffix.$lib_suffix"

mkdir -p $BUILD_DIR

if [ ! -x $host_godot ] || [ $force_host_rebuild -eq 1 ]
then
    rm -f $host_godot
    cd $GODOT_DIR
    scons p=$host_platform target=$host_target $host_build_options
    cp -vf $host_godot $BUILD_DIR/godot
fi

if [ $update_api -eq 1 ] || [ ! -f $BUILD_GDEXTENSION_DIR/extension_api.json ]
then
    mkdir -p $BUILD_GDEXTENSION_DIR
    cd $BUILD_GDEXTENSION_DIR
    $host_godot --headless --dump-extension-api
    cp -v $GODOT_DIR/core/extension/gdextension_interface.h $BUILD_GDEXTENSION_DIR/

    echo "Successfully updated the GDExtension API."
fi

if [ "$target_platform" = "" ]
then
    echo "No target selected."
    exit 0
fi

cd $GODOT_DIR
scons p=$target_platform target=$target arch=$target_arch $target_build_options swappy=no

function godot_to_android_arch() {
    godot_arch="$1"
    case "$godot_arch" in
        arm64)
            echo "arm64-v8a"
            ;;
        arm32)
            echo "armeabi-v7a"
            ;;
        *)
            echo "Unsupported arch: $godot_arch"
            exit 1
    esac
}

if [ "$target_platform" = "android" ]
then
    android_arch="$(godot_to_android_arch $target_arch)"
    android_build_type="release"
    android_target_build_type="release"
    if [ $debug -eq 1 ]
    then
        android_build_type="debug"
        android_target_build_type="debug"
    fi
    if [ $dev_build -eq 1 ]
    then
        android_build_type="dev"
        android_target_build_type="dev"
    fi
    if [ "$library_type" = "shared_library" ]
    then
        target_godot="$GODOT_DIR/platform/android/java/lib/libs/$android_build_type/${android_arch}/libgodot_android.so"
    fi
    mkdir -p $BUILD_DIR/android/$android_target_build_type/${android_arch}
    rm -f $BUILD_DIR/android/$android_target_build_type/${android_arch}/libgodot_android.*
    cp -vf $target_godot $BUILD_DIR/android/$android_target_build_type/${android_arch}/libgodot_android.$lib_suffix
fi
