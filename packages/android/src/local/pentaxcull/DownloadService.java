package local.pentaxcull;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * Foreground service that keeps downloads running with the screen off / the phone locked: a notification shows
 * the progress (with Cancel), a partial wake lock keeps the CPU on and a Wi-Fi lock keeps the camera link fast.
 * The work itself is done by {@link Downloads}; this service only keeps the app alive while it runs.
 */
public class DownloadService extends Service {
    static final String CHANNEL = "downloads";
    static final int ID_PROGRESS = 1, ID_DONE = 2;
    static final String ACTION_CANCEL = "local.pentaxcull.CANCEL";

    private PowerManager.WakeLock wake;
    private WifiManager.WifiLock wifi;

    static void start(Context c) {
        c.startForegroundService(new Intent(c, DownloadService.class));
    }

    static void stop(Context c) {
        c.stopService(new Intent(c, DownloadService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_CANCEL.equals(intent.getAction())) {
            Api.instance(this).downloads().cancel();
            return START_NOT_STICKY;
        }
        channel(this);
        startForeground(ID_PROGRESS, progress(this, getString(R.string.dl_starting), 0), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        if (wake == null) {
            wake = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "pentaxcull:download");
            wake.acquire(4 * 60 * 60 * 1000L);              // safety net: released in onDestroy
        }
        if (wifi == null) {
            wifi = getSystemService(WifiManager.class).createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "pentaxcull:download");
            wifi.acquire();
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        if (wake != null && wake.isHeld()) wake.release();
        if (wifi != null && wifi.isHeld()) wifi.release();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // ------------------------------------------------------------------ notifications
    static void channel(Context c) {
        NotificationChannel ch = new NotificationChannel(CHANNEL, c.getString(R.string.dl_channel), NotificationManager.IMPORTANCE_LOW);
        c.getSystemService(NotificationManager.class).createNotificationChannel(ch);
    }

    private static PendingIntent openApp(Context c) {
        Intent i = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(c, 0, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** Ongoing progress notification. percent: 0-100. */
    static Notification progress(Context c, String text, int percent) {
        PendingIntent cancel = PendingIntent.getService(c, 1, new Intent(c, DownloadService.class).setAction(ACTION_CANCEL),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(c, CHANNEL)
                .setSmallIcon(R.drawable.ic_notif)
                .setContentTitle(c.getString(R.string.dl_title))
                .setContentText(text)
                .setProgress(100, percent, percent == 0)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(openApp(c))
                .addAction(new Notification.Action.Builder(null, c.getString(R.string.dl_cancel), cancel).build())
                .build();
    }

    static void update(Context c, String text, int percent) {
        c.getSystemService(NotificationManager.class).notify(ID_PROGRESS, progress(c, text, percent));
    }

    /** Final notification (stays after the service stops; tap to open the app). */
    static void done(Context c, String text) {
        channel(c);
        Notification n = new Notification.Builder(c, CHANNEL)
                .setSmallIcon(R.drawable.ic_notif)
                .setContentTitle(c.getString(R.string.dl_title_done))
                .setContentText(text)
                .setAutoCancel(true)
                .setContentIntent(openApp(c))
                .build();
        c.getSystemService(NotificationManager.class).notify(ID_DONE, n);
    }
}
