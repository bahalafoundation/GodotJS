#ifndef GODOTJS_JSC_PCH_H
#define GODOTJS_JSC_PCH_H

#include "../../jsb.gen.h"
#include "../shared/jsb_custom_field.h"
#include "../../internal/jsb_internal.h"

#include "../../compat/jsb_ring_buffer.h"

//TODO WARNING: ONLY FOR DEV, NOT SUPPORTED TO BUILD. REMOVE IT AFTER jsc.impl IS READY.
#if !defined(API_AVAILABLE) && !defined(MACOS_ENABLED) && !defined(IOS_ENABLED)
#define API_AVAILABLE(...)
#endif

#include <JavaScriptCore/JavaScriptCore.h>

// Public JavaScriptCore API only: App Store Connect rejects a binary that references private JSC symbols (ITMS-90338).
// Weak handles use the JS WeakRef object rather than JSC's private JSWeak* C API (arcade#255, see jsb_jsc_handle.h).

// Apple headers define `nil` as a macro; this leaks into C++ code and can break
// identifiers named `nil` in non-ObjC translation units.
#ifdef nil
#undef nil
#endif

#include <memory>
#include <cstdint>

#define JSB_JSC_LOG(Severity, Format, ...) JSB_LOG_IMPL(jsc, Severity, Format, ##__VA_ARGS__)

#endif
