package com.aikid.terminal

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient

class UpdateWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val tokenStore = SecureTokenStore(applicationContext)
        if (tokenStore.token() == null) return Result.success()
        return runCatching {
            val api = DeviceApi(applicationContext, OkHttpClient(), tokenStore)
            val update = api.latestUpdate() ?: return Result.success()
            val apk = api.downloadUpdate(applicationContext, update)
            UpdateInstaller.install(applicationContext, apk)
            Result.success()
        }.getOrElse { Result.retry() }
    }
}

object UpdateScheduler {
    private const val FALLBACK_INTERVAL_HOURS = 6L
    private const val PERIODIC_NAME = "signed-app-update-periodic"
    private const val IMMEDIATE_NAME = "signed-app-update-immediate"
    private val networkConstraint = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()

    fun schedule(context: Context) {
        val work = PeriodicWorkRequestBuilder<UpdateWorker>(FALLBACK_INTERVAL_HOURS, TimeUnit.HOURS)
            .setConstraints(networkConstraint)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            PERIODIC_NAME,
            ExistingPeriodicWorkPolicy.KEEP,
            work,
        )
        runNow(context)
    }

    fun runNow(context: Context) {
        val work = OneTimeWorkRequestBuilder<UpdateWorker>()
            .setConstraints(networkConstraint)
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            IMMEDIATE_NAME,
            ExistingWorkPolicy.KEEP,
            work,
        )
    }
}
