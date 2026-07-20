package org.akshrava.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

import androidx.core.content.ContextCompat

class StopReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val stopIntent = Intent(context, AssistService::class.java).setAction(AssistService.ACTION_STOP)
        ContextCompat.startForegroundService(context, stopIntent)
    }
}
