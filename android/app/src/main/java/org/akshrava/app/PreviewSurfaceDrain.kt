package org.akshrava.app

import android.graphics.ImageFormat
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import androidx.camera.core.Preview
import androidx.camera.core.SurfaceRequest
import java.util.concurrent.atomic.AtomicInteger

/**
 * Headless Preview consumer for CameraX.
 *
 * Binding Preview alongside ImageAnalysis is required on some OEMs (incl. OnePlus) or analysis
 * buffers stay black / never arrive. A [android.graphics.SurfaceTexture] that is never drained
 * fills its buffer queue and stalls the whole capture session — including ImageAnalysis.
 * [ImageReader] drains frames without needing an EGL context or on-screen view.
 */
class PreviewSurfaceDrain {
    private var thread: HandlerThread? = null
    private var handler: Handler? = null
    private var reader: ImageReader? = null
    private val drained = AtomicInteger(0)

    fun attach(preview: Preview) {
        preview.setSurfaceProvider { request -> provide(request) }
    }

    private fun provide(request: SurfaceRequest) {
        release()
        val size = request.resolution
        val worker = HandlerThread("akshrava-preview-drain").also { it.start() }
        thread = worker
        val h = Handler(worker.looper).also { handler = it }
        // PRIVATE matches Preview's stream; 2 buffers so the producer is never blocked on us.
        val imageReader = ImageReader.newInstance(size.width, size.height, ImageFormat.PRIVATE, 2)
        reader = imageReader
        imageReader.setOnImageAvailableListener({ r ->
            try {
                r.acquireLatestImage()?.close()
                val n = drained.incrementAndGet()
                if (n == 1 || n % 30 == 0) {
                    Log.i("AkshravaVision", "preview drained frames=$n ${size.width}x${size.height}")
                }
            } catch (ex: Exception) {
                Log.w("AkshravaVision", "preview drain failed", ex)
            }
        }, h)
        val surface: Surface = imageReader.surface
        request.provideSurface(surface, { it.run() }) { result ->
            Log.i("AkshravaVision", "preview surface result=${result.resultCode}")
            // CameraX owns teardown signalling; release our reader when the request ends.
            if (result.resultCode != SurfaceRequest.Result.RESULT_SURFACE_USED_SUCCESSFULLY) {
                release()
            }
        }
    }

    fun release() {
        try {
            reader?.close()
        } catch (_: Exception) {
        }
        reader = null
        handler = null
        thread?.quitSafely()
        thread = null
        drained.set(0)
    }
}
