package org.akshrava.app

import android.os.Handler
import android.os.Looper
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry

/**
 * CameraX opens the device when the bound owner reaches STARTED; some OEM stacks only
 * fully stream once RESUMED. [androidx.lifecycle.LifecycleService] stays at STARTED, so
 * assistance uses this owner forced to RESUMED for the active session.
 */
class CameraLifecycleOwner : LifecycleOwner {
    private val registry = LifecycleRegistry(this)
    private val handler = Handler(Looper.getMainLooper())

    init {
        registry.currentState = Lifecycle.State.INITIALIZED
    }

    override val lifecycle: Lifecycle
        get() = registry

    fun resume() {
        handler.post {
            registry.currentState = Lifecycle.State.CREATED
            registry.currentState = Lifecycle.State.STARTED
            registry.currentState = Lifecycle.State.RESUMED
        }
    }

    fun destroy() {
        handler.post {
            if (registry.currentState == Lifecycle.State.INITIALIZED) return@post
            registry.currentState = Lifecycle.State.DESTROYED
        }
    }
}
