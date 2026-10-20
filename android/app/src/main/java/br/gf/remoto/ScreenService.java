package br.gf.remoto;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.provider.MediaStore;
import android.provider.Settings;
import android.util.DisplayMetrics;
import android.view.WindowManager;

import androidx.annotation.Nullable;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.List;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

/**
 * Transmite a tela DESTE celular para o PC pareado e repassa ao ControlService os toques
 * que o PC envia de volta. A sessão é sempre iniciada pelo usuário (a projeção de tela pede
 * confirmação do sistema) e mostra uma notificação fixa, com botão "Parar", enquanto dura.
 */
public class ScreenService extends Service {

    static final String ACTION_START = "start";
    static final String ACTION_STOP = "stop";
    private static final String CHANNEL = "controle";
    private static final int NOTIF = 42;

    static volatile boolean running = false;

    private MediaProjection projection;
    private VirtualDisplay vdisplay;
    private ImageReader reader;
    private HandlerThread thread;
    private Handler handler;
    private OkHttpClient http;
    private WebSocket ws;

    private int outW, outH, dpi;
    private long lastSent = 0;
    private volatile int fps = 7, quality = 45, maxW = 420;
    private volatile boolean paused = false;

    @Override
    public void onCreate() {
        super.onCreate();
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Controle pelo PC",
                    NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Avisa quando o PC está vendo este celular");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || ACTION_STOP.equals(intent.getAction())) {
            stopEverything();
            return START_NOT_STICKY;
        }
        startForeground(NOTIF, buildNotification());

        String host = intent.getStringExtra("host");
        String token = intent.getStringExtra("token");
        String name = intent.getStringExtra("name");
        int code = intent.getIntExtra("code", -1);
        Intent data = intent.getParcelableExtra("data");
        if (host == null || data == null) {
            stopEverything();
            return START_NOT_STICKY;
        }

        thread = new HandlerThread("tela-celular");
        thread.start();
        handler = new Handler(thread.getLooper());

        MediaProjectionManager mpm = (MediaProjectionManager) getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        projection = mpm.getMediaProjection(code, data);
        if (projection == null) {
            stopEverything();
            return START_NOT_STICKY;
        }
        projection.registerCallback(new MediaProjection.Callback() {
            @Override
            public void onStop() {
                stopEverything();
            }
        }, handler);

        setupDisplay();
        connect(host, token, name);
        running = true;
        notifyApp(true);
        return START_STICKY;
    }

    private void setupDisplay() {
        WindowManager wm = (WindowManager) getSystemService(Context.WINDOW_SERVICE);
        DisplayMetrics m = new DisplayMetrics();
        wm.getDefaultDisplay().getRealMetrics(m);
        dpi = m.densityDpi;
        float scale = Math.min(1f, (float) maxW / m.widthPixels);
        outW = Math.max(2, (Math.round(m.widthPixels * scale) / 2) * 2);
        outH = Math.max(2, (Math.round(m.heightPixels * scale) / 2) * 2);

        reader = ImageReader.newInstance(outW, outH, PixelFormat.RGBA_8888, 2);
        reader.setOnImageAvailableListener(this::onFrame, handler);
        vdisplay = projection.createVirtualDisplay("remotogf", outW, outH, dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader.getSurface(), null, handler);
    }

    private void onFrame(ImageReader r) {
        Image img = null;
        try {
            img = r.acquireLatestImage();
            if (img == null || paused || ws == null) return;
            long now = System.currentTimeMillis();
            if (now - lastSent < 1000 / fps) return;
            lastSent = now;

            Image.Plane plane = img.getPlanes()[0];
            ByteBuffer buf = plane.getBuffer();
            int pixelStride = plane.getPixelStride();
            int rowStride = plane.getRowStride();
            int rowPadding = rowStride - pixelStride * outW;
            Bitmap bmp = Bitmap.createBitmap(outW + rowPadding / pixelStride, outH, Bitmap.Config.ARGB_8888);
            bmp.copyPixelsFromBuffer(buf);
            Bitmap crop = (rowPadding == 0) ? bmp : Bitmap.createBitmap(bmp, 0, 0, outW, outH);

            ByteArrayOutputStream out = new ByteArrayOutputStream();
            crop.compress(Bitmap.CompressFormat.JPEG, quality, out);
            if (crop != bmp) crop.recycle();
            bmp.recycle();
            ws.send(ByteString.of(out.toByteArray()));
        } catch (Throwable ignored) {
        } finally {
            if (img != null) img.close();
        }
    }

    private void connect(String host, String token, String name) {
        http = new OkHttpClient.Builder()
                .pingInterval(20, TimeUnit.SECONDS)
                .readTimeout(0, TimeUnit.MILLISECONDS)
                .build();
        String url = "ws://" + host + "/wsphone?t=" + enc(token) + "&n=" + enc(name)
                + "&w=" + outW + "&h=" + outH;
        Request req = new Request.Builder().url(url).build();
        ws = http.newWebSocket(req, new WebSocketListener() {
            @Override
            public void onOpen(WebSocket w, Response resp) {
                sendInstalledApps(w);
            }

            @Override
            public void onMessage(WebSocket w, String text) {
                handleCommand(text);
            }

            @Override
            public void onFailure(WebSocket w, Throwable t, @Nullable Response resp) {
                stopEverything();
            }

            @Override
            public void onClosed(WebSocket w, int code, String reason) {
                stopEverything();
            }
        });
    }

    private void sendInstalledApps(WebSocket w) {
        try {
            PackageManager pm = getPackageManager();
            Intent main = new Intent(Intent.ACTION_MAIN, null).addCategory(Intent.CATEGORY_LAUNCHER);
            List<ResolveInfo> list = pm.queryIntentActivities(main, 0);
            JSONArray arr = new JSONArray();
            for (ResolveInfo ri : list) {
                if (ri.activityInfo == null) continue;
                String pkg = ri.activityInfo.packageName;
                if (pkg == null || pkg.equals(getPackageName())) continue;
                CharSequence lbl = ri.loadLabel(pm);
                JSONObject o = new JSONObject();
                o.put("nome", lbl != null ? lbl.toString() : pkg);
                o.put("pkg", pkg);
                arr.put(o);
            }
            JSONObject msg = new JSONObject();
            msg.put("apps", arr);
            w.send(msg.toString());
        } catch (Exception ignored) {
        }
    }

    private void launchPhoneApp(String pkg) {
        if (pkg == null || pkg.isEmpty()) return;
        try {
            PackageManager pm = getPackageManager();
            Intent i = null;
            if ("whatsapp".equalsIgnoreCase(pkg)) {
                i = pm.getLaunchIntentForPackage("com.whatsapp");
                if (i == null) i = pm.getLaunchIntentForPackage("com.whatsapp.w4b");
            } else if ("camera".equalsIgnoreCase(pkg)) {
                i = new Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA);
            } else if ("gallery".equalsIgnoreCase(pkg)) {
                i = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_APP_GALLERY);
            } else if ("settings".equalsIgnoreCase(pkg)) {
                i = new Intent(Settings.ACTION_SETTINGS);
            } else {
                i = pm.getLaunchIntentForPackage(pkg);
            }
            if (i != null) {
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
            }
        } catch (Exception ignored) {
        }
    }

    private void handleCommand(String text) {
        try {
            JSONObject d = new JSONObject(text);
            String t = d.optString("t");
            ControlService c = ControlService.instance;
            switch (t) {
                case "tap": if (c != null) c.tap(px(d, "x", outW), px(d, "y", outH)); break;
                case "long": if (c != null) c.longPress(px(d, "x", outW), px(d, "y", outH)); break;
                case "swipe": if (c != null) c.swipe(px(d, "x", outW), px(d, "y", outH),
                        px(d, "x2", outW), px(d, "y2", outH), d.optInt("ms", 200)); break;
                case "key": if (c != null) c.global(d.optString("k")); break;
                case "type": if (c != null) c.type(d.optString("text")); break;
                case "bksp": if (c != null) c.backspace(); break;
                case "open_app": launchPhoneApp(d.optString("pkg")); break;
                case "quality":
                    fps = Math.max(2, Math.min(d.optInt("fps", fps), 20));
                    quality = Math.max(20, Math.min(d.optInt("q", quality), 85));
                    break;
                case "pause": paused = d.optBoolean("on", false); break;
                case "stop": stopEverything(); break;
            }
        } catch (Exception ignored) {
        }
    }

    private float px(JSONObject d, String k, int size) {
        return (float) (d.optDouble(k, 0) * size);
    }

    private Notification buildNotification() {
        Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, ScreenService.class).setAction(ACTION_STOP);
        PendingIntent sp = PendingIntent.getService(this, 1, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return b.setSmallIcon(android.R.drawable.ic_menu_view)
                .setContentTitle("Seu PC está controlando este celular")
                .setContentText("Toque para abrir o Remoto GF ou use Parar para encerrar.")
                .setOngoing(true)
                .setContentIntent(pi)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Parar", sp)
                .build();
    }

    private void notifyApp(boolean on) {
        Intent i = new Intent("br.gf.remoto.CONTROL_STATE").putExtra("on", on);
        i.setPackage(getPackageName());
        sendBroadcast(i);
    }

    private void stopEverything() {
        if (!running && ws == null && projection == null) {
            stopForeground(true);
            stopSelf();
            return;
        }
        running = false;
        paused = false;
        try { if (ws != null) ws.close(1000, "fim"); } catch (Exception ignored) {}
        ws = null;
        try { if (vdisplay != null) vdisplay.release(); } catch (Exception ignored) {}
        try { if (reader != null) reader.close(); } catch (Exception ignored) {}
        try { if (projection != null) projection.stop(); } catch (Exception ignored) {}
        projection = null;
        if (thread != null) thread.quitSafely();
        notifyApp(false);
        stopForeground(true);
        stopSelf();
    }

    private static String enc(String s) {
        try {
            return java.net.URLEncoder.encode(s == null ? "" : s, "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    @Override
    public void onDestroy() {
        stopEverything();
        super.onDestroy();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
