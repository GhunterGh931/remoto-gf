package br.gf.remoto;

import android.app.Notification;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/**
 * Mantém o Remoto GF vivo enquanto há download/render acontecendo no PC.
 * Usa a própria notificação de progresso como notificação do serviço, então o Android
 * não fecha o app em segundo plano e a barra continua atualizando.
 */
public class ProgressService extends Service {

    public static volatile boolean running = false;
    public static volatile int currentId = -1;
    public static final String ACTION_STOP_DETACH = "br.gf.remoto.PROGRESS_STOP_DETACH";
    public static final String ACTION_STOP_REMOVE = "br.gf.remoto.PROGRESS_STOP_REMOVE";

    @Override
    @SuppressWarnings("deprecation")
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_STOP_DETACH.equals(action) || ACTION_STOP_REMOVE.equals(action)) {
            // DETACH: a notificação fica (vira o aviso de concluído); REMOVE: some junto
            stopForeground(ACTION_STOP_DETACH.equals(action) ? STOP_FOREGROUND_DETACH : STOP_FOREGROUND_REMOVE);
            running = false;
            currentId = -1;
            stopSelf();
            return START_NOT_STICKY;
        }
        Notification n = intent != null ? intent.getParcelableExtra("notif") : null;
        int id = intent != null ? intent.getIntExtra("id", 5000) : 5000;
        if (n == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(id, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else {
                startForeground(id, n);
            }
            running = true;
            currentId = id;
        } catch (Exception e) {
            running = false;
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        currentId = -1;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
