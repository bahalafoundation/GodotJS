#ifndef GODOTJS_JSC_HANDLE_H
#define GODOTJS_JSC_HANDLE_H

#include "jsb_jsc_pch.h"
#include "jsb_jsc_data.h"
#include "jsb_jsc_broker.h"
#include "jsb_jsc_ext.h"

namespace jsb::impl
{
    class Helper;
}

namespace v8
{
    template <typename T>
    class Global;

    template <typename T>
    class MaybeLocal;

    template <typename T>
    class Local
    {
        // static_assert(sizeof(T) == sizeof(Data));

        template <typename S>
        friend class Global;

        template <typename S>
        friend class Local;

        template <typename S>
        friend class MaybeLocal;

        friend class Isolate;
        friend class Context;
        friend class jsb::impl::Helper;

    public:
        Local() = default;

        template<typename S>
        Local(Local<S> other): data_(other.data_) {}

        bool IsEmpty() const { return !data_.isolate_; }

        T* operator->() const { return (T*) &const_cast<Local*>(this)->data_; }

        explicit operator JSValueRef() const
        {
            return data_.isolate_ ? jsb::impl::Broker::stack_val(data_.isolate_, data_.stack_pos_) : nullptr;
        }

        template <typename S>
        Local<S> As() const { return Local<S>(data_); }

        Local(const Data data): data_(data) {}

        template <typename S>
        bool operator==(const Local<S>& other) const
        {
            return data_ == other.data_;
        }

        template <typename S>
        bool operator!=(const Local<S>& other) const
        {
            return !operator==(other);
        }

        template <typename S>
        bool operator==(const Global<S>& other) const;

        template <typename S>
        bool operator!=(const Global<S>& other) const;

    private:
        Data data_;
    };

    template<typename T>
    class MaybeLocal
    {
        template <typename S>
        friend class MaybeLocal;

    public:
        MaybeLocal() = default;
        MaybeLocal(const Data data): data_(data) {}

        template<typename S>
        MaybeLocal(MaybeLocal<S> other) : data_(other.data_) {}

        template<typename S>
        MaybeLocal(Local<S> other) : data_(other.data_) {}

        bool IsEmpty() const { return !data_.isolate_; }
        Local<T> ToLocalChecked()
        {
            jsb_check(!IsEmpty());
            return Local<T>(data_);
        }

        bool ToLocal(Local<T>* out) const
        {
            *out = Local<T>(data_);
            return !IsEmpty();
        }

    private:
        Data data_;
    };

    enum class WeakCallbackType
    {
        kParameter,
        kInternalFields,
    };

    template<typename T>
    class WeakCallbackInfo
    {
    public:
        using Callback = void (*)(const WeakCallbackInfo& data);

        WeakCallbackInfo(Isolate* isolate, T* parameter, void** internal_fields): isolate_(isolate), parameter_(parameter), internal_fields_(internal_fields) {}
        Isolate* GetIsolate() const { return isolate_; }
        T* GetParameter() const { return parameter_; }
        void* GetInternalField(int index) const { jsb_check(index == 0 || index == 1); return internal_fields_[index]; }

    private:
        Isolate* isolate_;
        T* parameter_;
        void** internal_fields_;
    };

    // A persistent handle. Strong: `value_`, protected with JSValueProtect. Weak: `shadow_`, a protected JS `WeakRef` to the
    // object, and `value_` is unused.
    //
    // Weak handles go through WeakRef (created and dereferenced with the public C API, see Isolate::_NewWeakRef) because
    // JavaScriptCore's C API has no public weak handle. The one it has, JSWeakCreate/JSWeakGetObject/JSWeakRelease, is
    // private SPI, and App Store Connect rejects any binary that references it (ITMS-90338, arcade#255). A WeakRef has
    // the liveness a JSWeak had: JSC clears both at the end of the collection that finds the object unreachable, before
    // the object is swept and its class finalizer runs. So is_alive() never reports a dead object as alive, and the weak
    // callback keeps coming from the class finalizer (Isolate::_BridgeInstance_finalizer), exactly as before.
    //
    // WeakRef.prototype.deref() keeps its target alive until the current synchronous run of JS ends (ECMA-262 KeepDuringJob;
    // for JSC that is when the API lock is released or microtasks drain). A deref from native code outside of JS therefore
    // pins nothing; one inside a JS->native call pins the object only until that outermost call returns.
    template <typename T>
    class Global
    {
        enum WeakType { kStrong, kWeak, kWeakCallback, };

        // clear all fields silently after moved
        void _clear()
        {
            isolate_ = nullptr;
            shadow_ = nullptr;
            value_ = nullptr;
            internal_data_ = nullptr;
            weak_type_ = WeakType::kStrong;
        }

        // Disarm the weak callback registered by SetWeak(parameter, callback), whether or not the JS object is still alive.
        //
        // JSC only tells us about a dead object through the class finalizer, which runs lazily (sweep time, any thread) and
        // which the isolate merely queues into `pending_finalize_`. Between the collection and that queued callback running,
        // `shadow_` already reads null, so the object can't be reached through JSObjectGetPrivate any more; the InternalData
        // carrying the callback is still alive though (it is only deleted after the queued callback ran). Going through the
        // pointer we saved in SetWeak() lets Reset() cancel the callback in that window, which is what makes it safe to bind
        // the same native pointer to a fresh JS object: the stale callback will not run against the new binding.
        //
        // Invariant relied upon: an InternalData outlives every Global that points at it in kWeakCallback state. The isolate
        // deletes it right after running its callback, and every callback registered through a Global resets that Global
        // (or the Global was moved out and reset earlier, see Environment::free_object), clearing `internal_data_` here.
        void _clear_weak_callback()
        {
            if (weak_type_ == WeakType::kWeakCallback)
            {
                jsb::impl::Broker::ClearWeakCallback(internal_data_);
                internal_data_ = nullptr;
                return;
            }
            // kWeak: nothing was registered by this handle, keep the previous behaviour (a no-op once the object is dead)
            jsb::impl::Broker::SetWeak(isolate_, jsb::impl::Broker::DerefWeakRef(isolate_, shadow_), nullptr, nullptr);
        }

    public:
        Global() = default;
        Global(Isolate* isolate, Local<T> value) { Reset(isolate, value); }

        Global(const Global&) = delete;
        Global& operator=(const Global&) = delete;

        ~Global() { Reset(); }

        Global(Global&& other) noexcept
        {
            isolate_ = other.isolate_;
            weak_type_ = other.weak_type_;
            shadow_ = other.shadow_;
            value_ = other.value_;
            internal_data_ = other.internal_data_;
            other._clear();
        }

        template <typename S>
        Global& operator=(Global<S>&& other)
        {
            if (this != &other)
            {
                Reset();
                if (!other.IsEmpty())
                {
                    isolate_ = other.isolate_;
                    weak_type_ = other.weak_type_;
                    shadow_ = other.shadow_;
                    value_ = other.value_;
                    internal_data_ = other.internal_data_;
                    other._clear();
                }
            }
            return *this;
        }

        void Reset()
        {
            if (!isolate_) return;

            switch (weak_type_)
            {
            case WeakType::kStrong:
                {
                    // release if strong referenced
                    const JSContextRef ctx = jsb::impl::Broker::ctx(isolate_);
                    JSValueUnprotect(ctx, value_);
                    value_ = nullptr;
                    break;
                }
            case WeakType::kWeak:
            case WeakType::kWeakCallback:
                {
                    // clear callback (also when the object is already collected, see _clear_weak_callback)
                    _clear_weak_callback();
                    jsb::impl::Broker::ReleaseWeakRef(isolate_, shadow_);
                    shadow_ = nullptr;
                    break;
                }
            default: break;
            }

            jsb::impl::Broker::_remove_reference(isolate_);

            isolate_ = nullptr;
            weak_type_ = WeakType::kStrong;
        }

        void Reset(Isolate* isolate, Local<T> value)
        {
            Reset();

            jsb_check(isolate);
            isolate_ = isolate;

            // ensure the runtime alive
            jsb::impl::Broker::_add_reference(isolate_);

            if (!value.IsEmpty())
            {
                // protected
                value_ = jsb::impl::Broker::stack_dup(isolate_, value.data_.stack_pos_);
                shadow_ = nullptr;
                weak_type_ = WeakType::kStrong;
            }
        }

        void Reset(Isolate* isolate, const Global& value)
        {
            Reset(isolate, value.Get(isolate));
        }

        void ClearWeak()
        {
            jsb_check(isolate_ && weak_type_ != WeakType::kStrong);
            const JSContextRef ctx = jsb::impl::Broker::ctx(isolate_);

            // one deref, protected at once: the object can't be collected between the liveness check and the protect
            const JSObjectRef obj = jsb::impl::Broker::DerefWeakRef(isolate_, shadow_);
            jsb_check(obj);

            if (weak_type_ == WeakType::kWeakCallback)
            {
                // clear callback
                _clear_weak_callback();
            }

            weak_type_ = WeakType::kStrong;
            value_ = obj;
            JSValueProtect(ctx, value_);
            jsb::impl::Broker::ReleaseWeakRef(isolate_, shadow_);
            shadow_ = nullptr;
        }

        // ClearWeak() before SetWeak() if SetWeak(parameter) called priorly
        void SetWeak()
        {
            jsb_check(isolate_ && weak_type_ == WeakType::kStrong);
            const JSContextRef ctx = jsb::impl::Broker::ctx(isolate_);

            weak_type_ = WeakType::kWeak;
            const JSObjectRef obj = jsb::impl::JavaScriptCore::AsObject(ctx, value_);
            shadow_ = jsb::impl::Broker::NewWeakRef(isolate_, obj);
            jsb::impl::Broker::SetWeak(isolate_, obj, nullptr, nullptr);
            JSValueUnprotect(ctx, value_);
            value_ = nullptr;
        }

        template<typename S>
        void SetWeak(S* parameter, typename WeakCallbackInfo<S>::Callback callback, v8::WeakCallbackType type)
        {
            jsb_check(isolate_ && weak_type_ == WeakType::kStrong);
            const JSContextRef ctx = jsb::impl::Broker::ctx(isolate_);
            jsb_check(JSValueIsObject(ctx, value_));

            weak_type_ = WeakType::kWeakCallback;
            const JSObjectRef obj = jsb::impl::JavaScriptCore::AsObject(ctx, value_);
            shadow_ = jsb::impl::Broker::NewWeakRef(isolate_, obj);
            // remember where the callback lives so Reset() can disarm it after `obj` is collected (see _clear_weak_callback)
            internal_data_ = jsb::impl::Broker::GetInternalData(obj);
            jsb::impl::Broker::SetWeak(isolate_, obj, parameter, (void*) callback);
            JSValueUnprotect(ctx, value_);
            value_ = nullptr;
        }

        // Return true if no value held by this handle, or dead for a weak handle.
        bool IsEmpty() const { return !isolate_ || !is_alive(); }

        Local<T> Get(Isolate* isolate) const
        {
            jsb_check(isolate_ == isolate && isolate_ && is_alive());
            return Local<T>(Data(isolate_, jsb::impl::Broker::push_copy(isolate_, (JSValueRef) *this)));
        }

        explicit operator JSValueRef() const
        {
            jsb_check(isolate_);
            if (weak_type_ == WeakType::kStrong) return value_;
            return jsb::impl::Broker::DerefWeakRef(isolate_, shadow_);
        }

        template <typename S>
        bool operator==(const Global<S>& other) const
        {
            return jsb::impl::Broker::IsStrictEqual(isolate_,
                weak_type_ != WeakType::kStrong ? jsb::impl::Broker::DerefWeakRef(isolate_, shadow_) : value_,
                other.weak_type_ != WeakType::kStrong ? jsb::impl::Broker::DerefWeakRef(other.isolate_, other.shadow_) : other.value_);
        }

        template <typename S>
        bool operator!=(const Global<S>& other) const
        {
            return !operator==(other);
        }

        template <typename S>
        bool operator==(const Local<S>& other) const
        {
            return other == *this;
        }

        template <typename S>
        bool operator!=(const Local<S>& other) const
        {
            return other != *this;
        }

    private:
        // A strong handle is always alive. A weak one is alive until its WeakRef's target is collected.
        bool is_alive() const { return weak_type_ == WeakType::kStrong || !!jsb::impl::Broker::DerefWeakRef(isolate_, shadow_); }

        Isolate* isolate_ = nullptr;

        // only used for weak handle: a protected `new WeakRef(object)` (see the class comment)
        JSObjectRef shadow_ = nullptr;

        // value_ is not protected if this handle is weak, check is_alive() before accessing value_
        JSValueRef value_ = nullptr;

        // only used for kWeakCallback: the InternalData (JSObjectGetPrivate of the object) holding the registered callback.
        // Valid until _clear_weak_callback(), even after the object itself is collected (see the invariant there).
        void* internal_data_ = nullptr;

        WeakType weak_type_ = WeakType::kStrong;
    };

    template <typename T>
    template <typename S>
    bool Local<T>::operator==(const Global<S>& other) const
    {
        return this->operator==(other.Get(data_.isolate_));
    }

    template <typename T>
    template <typename S>
    bool Local<T>::operator!=(const Global<S>& other) const
    {
        return !operator==(other);
    }

}

#endif
