#ifndef GODOTJS_JSC_BROKER_H
#define GODOTJS_JSC_BROKER_H
#include "jsb_jsc_pch.h"

namespace v8
{
    class Isolate;
}

namespace jsb::impl
{
    // a helper class to break header cyclic dependencies
    class Broker
    {
    public:
        static void SetWeak(v8::Isolate* isolate, JSObjectRef value, void* parameter, void* callback);

        // the InternalData of a bridge instance (JSObjectGetPrivate), nullptr for any other JS object
        static void* GetInternalData(JSObjectRef value);

        // clear the weak callback stored in an InternalData obtained from GetInternalData().
        // unlike SetWeak(isolate, obj, nullptr, nullptr) this works after the JS object is collected (see v8::Global::_clear_weak_callback)
        static void ClearWeakCallback(void* internal_data);

        // see v8::Isolate::_NewWeakRef/_DerefWeakRef/_ReleaseWeakRef
        static JSObjectRef NewWeakRef(v8::Isolate* isolate, JSObjectRef target);
        static JSObjectRef DerefWeakRef(v8::Isolate* isolate, JSObjectRef weak_ref);
        static void ReleaseWeakRef(v8::Isolate* isolate, JSObjectRef weak_ref);

        static JSContextGroupRef rt(v8::Isolate* isolate);
        static JSContextRef ctx(v8::Isolate* isolate);

        // peek JSValue on stack (without duplicating)
        static JSValueRef stack_val(v8::Isolate* isolate, uint16_t index);

        // copy JSValue on stack (with duplicating)
        static JSValueRef stack_dup(v8::Isolate* isolate, uint16_t index);

        static uint16_t push_copy(v8::Isolate* isolate, JSValueRef value);

        static void _add_reference(v8::Isolate* isolate);
        static void _remove_reference(v8::Isolate* isolate);

        // strict eq check
        static bool IsStrictEqual(v8::Isolate* isolate, JSValueRef a, JSValueRef b);

    };
}
#endif
