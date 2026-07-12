package com.aikid.terminal

import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build

class KioskAdminReceiver : DeviceAdminReceiver()

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED ||
            intent.action == Intent.ACTION_LOCKED_BOOT_COMPLETED ||
            intent.action == Intent.ACTION_MY_PACKAGE_REPLACED
        ) {
            context.startActivity(
                Intent(context, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            )
        }
    }
}

object Kiosk {
    fun isDeviceOwner(context: Context): Boolean =
        context.getSystemService(DevicePolicyManager::class.java)
            .isDeviceOwnerApp(context.packageName)

    fun enter(activity: MainActivity) {
        val manager = activity.getSystemService(DevicePolicyManager::class.java)
        val admin = ComponentName(activity, KioskAdminReceiver::class.java)
        if (isDeviceOwner(activity)) {
            manager.setLockTaskPackages(admin, arrayOf(activity.packageName))
            if (Build.VERSION.SDK_INT >= 28) {
                manager.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_NONE)
            }
            manager.setStatusBarDisabled(admin, true)
            manager.setKeyguardDisabled(admin, true)
            manager.addPersistentPreferredActivity(
                admin,
                IntentFilter(Intent.ACTION_MAIN).apply {
                    addCategory(Intent.CATEGORY_HOME)
                    addCategory(Intent.CATEGORY_DEFAULT)
                },
                ComponentName(activity, MainActivity::class.java),
            )
        }
        activity.startLockTask()
    }

    fun exit(activity: MainActivity) {
        val manager = activity.getSystemService(DevicePolicyManager::class.java)
        if (isDeviceOwner(activity)) {
            val admin = ComponentName(activity, KioskAdminReceiver::class.java)
            manager.setStatusBarDisabled(admin, false)
            manager.setKeyguardDisabled(admin, false)
            manager.clearPackagePersistentPreferredActivities(admin, activity.packageName)
        }
        runCatching { activity.stopLockTask() }
    }
}
