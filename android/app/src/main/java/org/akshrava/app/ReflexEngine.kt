package org.akshrava.app

import android.content.Context
import java.io.File

/**
 * Offline reflex path. Without a licensed `model.tflite` asset this stays fail-closed:
 * never invents detections or ships placeholder weights.
 */
interface ReflexEngine {
    fun evaluate(frame: EncodedFrame): String?
    fun isArmed(): Boolean

    /** Release model/interpreter resources on session teardown. No-op for the disabled engine. */
    fun release() {}
}

class DisabledReflexEngine : ReflexEngine {
    override fun evaluate(frame: EncodedFrame): String? = null
    override fun isArmed(): Boolean = false
}

object ReflexFactory {
    private const val ASSET_NAME = "model.tflite"

    fun create(context: Context): ReflexEngine {
        // Fail closed: no bundled fake YOLO/TFLite weights. Only arm if an operator-provided
        // file exists on disk (never invent or ship placeholder weights in-repo).
        val external = File(context.filesDir, ASSET_NAME)
        if (!external.isFile || external.length() < 1024) {
            return DisabledReflexEngine()
        }
        // A real TFLite interpreter path is intentionally not wired until licensed weights
        // and SHA pinning land. Presence alone must not invent obstacle speech.
        return DisabledReflexEngine()
    }
}
