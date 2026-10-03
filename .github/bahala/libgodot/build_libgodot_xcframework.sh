#!/bin/bash

set -eux

BASE_DIR="$( cd "$(dirname "$0")" ; pwd -P )"

GODOT_DIR="$BASE_DIR/godot"
GODOT_CPP_DIR="$BASE_DIR/godot-cpp"
BUILD_DIR=$BASE_DIR/build
LIBGODOT_FRAMEWORK_DIR=$BASE_DIR/libgodot_framework

host_system="$(uname -s)"
host_arch="$(uname -m)"
target="template_debug"
build_type="debug"
config="Debug"
suffix=""
simulator=1
target_build=1

case "$host_system" in
    Darwin)
        cpus="$(sysctl -n hw.logicalcpu)"
    ;;
    *)
        echo "System $host_system is unsupported"
        exit 1
    ;;
esac


while [ "${1:-}" != "" ]
do
    case "$1" in
        --target)
            shift
            target="${1:-}"
        ;;
        --no-sim)
            simulator=0
        ;;
        --no-target)
            target_build=0
        ;;
        *)
            echo "Usage: $0 [--target <template_debug|template_dev|template_release|editor>] [--no-sim] [--no-target]"
            exit 1
        ;;
    esac
    shift
done

if [ "$target" = "template_debug" ]
then
    suffix=""
    config="Debug"
fi

if [ "$target" = "template_dev" ]
then
    suffix=".dev"
    target="template_debug"
    config="Debug"
    build_type="dev"
fi

if [ "$target" = "template_release" ]
then
    config="Release"
    build_type="release"
fi

mkdir -p $BUILD_DIR

cp $GODOT_DIR/core/extension/gdextension_interface.h $LIBGODOT_FRAMEWORK_DIR/libgodot/gdextension_interface.h
cp $GODOT_DIR/core/extension/libgodot.h $LIBGODOT_FRAMEWORK_DIR/libgodot/libgodot.h

sed -i '' -e 's|^#include "gdextension_interface.h"|#include <libgodot/gdextension_interface.h>|' $LIBGODOT_FRAMEWORK_DIR/libgodot/libgodot.h

if [ $target_build -eq 1 ]
then

cp -a $GODOT_DIR/bin/libgodot.ios.$target$suffix.arm64.a $LIBGODOT_FRAMEWORK_DIR/libgodot/libgodot.a

rm -rf $BUILD_DIR/libgodot.framework*
rm -rf $BUILD_DIR/ios_${target}
cd $LIBGODOT_FRAMEWORK_DIR
xcodebuild -scheme libgodot -configuration $config -destination 'generic/platform=iOS' clean build

cd $BASE_DIR
mkdir -p $BUILD_DIR/ios_${target}
mv $BUILD_DIR/libgodot.framework $BUILD_DIR/ios_${target}/
mv $BUILD_DIR/libgodot.framework.dSYM $BUILD_DIR/ios_${target}/

fi

if [ $simulator -eq 1 ]
then
    echo "Simulator build enabled"
    if [ -f $GODOT_DIR/bin/libgodot.ios.$target$suffix.arm64.simulator.a ] || [ -f $GODOT_DIR/bin/libgodot.ios.$target$suffix.x86_64.simulator.a ]
    then
        simulator_files=""
        for simarch in arm64 x86_64
        do
            if [ -f $GODOT_DIR/bin/libgodot.ios.$target$suffix.$simarch.simulator.a ]
            then
                simulator_files="$simulator_files $GODOT_DIR/bin/libgodot.ios.$target$suffix.$simarch.simulator.a"
            fi
        done
        echo "Building for Simulator"
        rm -rf $BUILD_DIR/ios_${target}_simulator
        lipo -create -output $LIBGODOT_FRAMEWORK_DIR/libgodot/libgodot.a $simulator_files
        rm -rf $BUILD_DIR/libgodot.framework*
        cd $LIBGODOT_FRAMEWORK_DIR
        xcodebuild -scheme libgodot -configuration $config -destination 'generic/platform=iOS Simulator' clean build

        cd $BASE_DIR
        mkdir -p $BUILD_DIR/ios_${target}_simulator
        mv $BUILD_DIR/libgodot.framework $BUILD_DIR/ios_${target}_simulator/
        mv $BUILD_DIR/libgodot.framework.dSYM $BUILD_DIR/ios_${target}_simulator/
    fi
fi

inputs=""
for d in $BUILD_DIR/ios_${target}*
do
    inputs="$inputs -framework $d/libgodot.framework"
    inputs="$inputs -debug-symbols $d/libgodot.framework.dSYM"
done

tmp_dir="$(mktemp -d)"


xcodebuild -create-xcframework \
    $inputs \
    -output "$tmp_dir/libgodot.xcframework"

if [ "$target" = "template_release" ]
then
    find "$tmp_dir/libgodot.xcframework/ios-arm64" -name 'libgodot' | grep 'libgodot\.framework/libgodot$' | xargs strip -u -r
fi

BUILD_LIBGODOT_TARGET_DIR="$BUILD_DIR/libgodot/$build_type"
rm -rf $BUILD_LIBGODOT_TARGET_DIR
mkdir -p $BUILD_LIBGODOT_TARGET_DIR

cd $tmp_dir
cp -Rvf libgodot.xcframework $BUILD_LIBGODOT_TARGET_DIR

rm -rf $tmp_dir
