package br.gf.remoto;

import android.app.Activity;
import android.app.DownloadManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.BroadcastReceiver;
import android.content.IntentFilter;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.media.projection.MediaProjectionManager;
import android.os.Environment;
import android.provider.Settings;
import android.text.TextUtils;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;

import android.database.Cursor;
import android.provider.OpenableColumns;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.ArrayList;
import java.util.List;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;

/**
 * Remoto GF para Android.
 * A interface é a mesma do PC (pasta web/), e esta Activity entrega a ela o que só um app
 * instalado consegue: botões de volume, notificações do sistema, giroscópio, tela ligada,
 * orientação e a busca automática do PC na rede.
 */
public class MainActivity extends Activity implements SensorEventListener {

    private static final String CHANNEL = "avisos";
    private static final String CHANNEL_DL = "downloads";
    private static final int DISCOVERY_PORT = 8765;

    private WebView web;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private volatile boolean volumeKeys = false;
    private SensorManager sensors;
    private Sensor gyro;
    private boolean gyroOn = false;
    private int notifId = 100;

    private static final int REQ_PROJECTION = 7001;
    private static final int REQ_FILE_CHOOSER = 7002;
    private static final int REQ_CAM_PERM = 7003;
    private static final int REQ_SPEECH = 7004;
    private String pendingHost, pendingToken, pendingName;
    private ValueCallback<Uri[]> fileCallback;
    private PermissionRequest pendingWebPerm;
    private BroadcastReceiver controlRx;
    // "Compartilhar > Enviar ao PC": o que outro app mandou para cá
    private final List<Uri> sharedUris = new ArrayList<>();
    private volatile String pendingShareJson = null;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(0xFF101216);
        getWindow().setNavigationBarColor(0xFF15181D);

        web = new WebView(this);
        web.setBackgroundColor(0xFF101216);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setTextZoom(100);

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                ui.post(() -> {
                    if (Build.VERSION.SDK_INT >= 23 && checkSelfPermission("android.permission.CAMERA") != PackageManager.PERMISSION_GRANTED) {
                        pendingWebPerm = request;
                        requestPermissions(new String[]{"android.permission.CAMERA", "android.permission.RECORD_AUDIO"}, REQ_CAM_PERM);
                    } else {
                        request.grant(request.getResources());
                    }
                });
            }

            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> filePathCallback,
                                             FileChooserParams fileChooserParams) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = filePathCallback;
                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("*/*");
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try {
                    startActivityForResult(Intent.createChooser(intent, "Escolher arquivo(s)"), REQ_FILE_CHOOSER);
                    return true;
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    return false;
                }
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if ("file".equals(u.getScheme())) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException ignored) {
                }
                return true;
            }
        });
        web.addJavascriptInterface(new Bridge(), "Android");
        web.loadUrl("file:///android_asset/index.html");
        handleShare(getIntent());

        sensors = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        if (sensors != null) gyro = sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Avisos do PC",
                    NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("Pedidos de autorização e avisos do Remoto GF");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
            // downloads em andamento: sem som, só a barra de progresso na notificação
            NotificationChannel dl = new NotificationChannel(CHANNEL_DL, "Downloads no PC",
                    NotificationManager.IMPORTANCE_LOW);
            dl.setDescription("Progresso dos downloads do PC (Baixador Drive GF e outros apps GF)");
            if (nm != null) nm.createNotificationChannel(dl);
        }
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);
        }

        controlRx = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent i) {
                js("window.onControlState&&window.onControlState(" + i.getBooleanExtra("on", false) + ")");
            }
        };
        IntentFilter f = new IntentFilter("br.gf.remoto.CONTROL_STATE");
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(controlRx, f, Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(controlRx, f);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleShare(intent);
    }

    /** Recebe arquivos, links ou texto do menu Compartilhar do Android. */
    @SuppressWarnings("deprecation")
    private void handleShare(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (!Intent.ACTION_SEND.equals(action) && !Intent.ACTION_SEND_MULTIPLE.equals(action)) return;
        sharedUris.clear();
        try {
            if (Intent.ACTION_SEND_MULTIPLE.equals(action)) {
                ArrayList<Uri> list = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
                if (list != null) sharedUris.addAll(list);
            } else {
                Uri u = intent.getParcelableExtra(Intent.EXTRA_STREAM);
                if (u != null) sharedUris.add(u);
            }
        } catch (Exception ignored) {
        }
        try {
            JSONObject j = new JSONObject();
            CharSequence txt = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
            j.put("text", txt != null ? txt.toString() : "");
            JSONArray names = new JSONArray();
            for (Uri u : sharedUris) names.put(displayName(u));
            j.put("files", names);
            pendingShareJson = j.toString();
        } catch (Exception ignored) {
        }
        // não processa de novo ao girar a tela ou voltar ao app
        intent.setAction(Intent.ACTION_MAIN);
        js("window.onShared&&window.onShared()");
    }

    private String displayName(Uri u) {
        String name = null;
        try (Cursor c = getContentResolver().query(u, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) name = c.getString(0);
        } catch (Exception ignored) {
        }
        if (name == null || name.trim().isEmpty()) {
            name = u.getLastPathSegment();
            if (name == null || name.trim().isEmpty()) name = "arquivo_" + System.currentTimeMillis();
        }
        return name.replaceAll("[\\\\/:*?\"<>|]", "_");
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req == REQ_SPEECH) {
            ArrayList<String> r = (res == RESULT_OK && data != null)
                    ? data.getStringArrayListExtra(android.speech.RecognizerIntent.EXTRA_RESULTS) : null;
            if (r != null && !r.isEmpty()) js("window.onDictation&&window.onDictation(" + JSONObject.quote(r.get(0)) + ")");
            return;
        }
        if (req == REQ_FILE_CHOOSER) {
            if (fileCallback == null) return;
            Uri[] results = null;
            if (res == RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    int n = data.getClipData().getItemCount();
                    results = new Uri[n];
                    for (int i = 0; i < n; i++) results[i] = data.getClipData().getItemAt(i).getUri();
                } else if (data.getData() != null) {
                    results = new Uri[]{data.getData()};
                }
            }
            fileCallback.onReceiveValue(results);
            fileCallback = null;
            return;
        }
        if (req != REQ_PROJECTION) return;
        if (res != RESULT_OK || data == null) {
            js("window.onControlDenied&&window.onControlDenied()");
            return;
        }
        Intent svc = new Intent(this, ScreenService.class).setAction(ScreenService.ACTION_START)
                .putExtra("code", res).putExtra("data", data)
                .putExtra("host", pendingHost).putExtra("token", pendingToken).putExtra("name", pendingName);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(svc);
        else startService(svc);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_CAM_PERM && pendingWebPerm != null) {
            try {
                pendingWebPerm.grant(pendingWebPerm.getResources());
            } catch (Exception ignored) {
            }
            pendingWebPerm = null;
        }
    }

    private void js(final String code) {
        ui.post(() -> {
            if (web != null) web.evaluateJavascript(code, null);
        });
    }

    // ---------------------------------------------------------- botões de volume
    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int k = e.getKeyCode();
        if (volumeKeys && (k == KeyEvent.KEYCODE_VOLUME_UP || k == KeyEvent.KEYCODE_VOLUME_DOWN)) {
            if (e.getAction() == KeyEvent.ACTION_DOWN) {
                js("window.onVolumeKey&&window.onVolumeKey('" + (k == KeyEvent.KEYCODE_VOLUME_UP ? "up" : "down") + "')");
            }
            return true; // não mexe no volume do celular
        }
        return super.dispatchKeyEvent(e);
    }

    // ---------------------------------------------------------- voltar
    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        web.evaluateJavascript("(window.onNativeBack&&window.onNativeBack())?'1':'0'", value -> {
            if (value == null || !value.contains("1")) MainActivity.super.onBackPressed();
        });
    }

    // ---------------------------------------------------------- ciclo de vida
    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        if (gyroOn && gyro != null) sensors.registerListener(this, gyro, SensorManager.SENSOR_DELAY_GAME);
        js("window.onNativeResume&&window.onNativeResume()");
    }

    @Override
    protected void onPause() {
        if (sensors != null) sensors.unregisterListener(this);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        try { if (controlRx != null) unregisterReceiver(controlRx); } catch (Exception ignored) {}
        if (web != null) web.destroy();
        super.onDestroy();
    }

    // ---------------------------------------------------------- giroscópio
    @Override
    public void onSensorChanged(SensorEvent ev) {
        if (!gyroOn) return;
        // rad/s: z = girar para os lados, x = inclinar para cima/baixo
        float z = ev.values[2], x = ev.values[0];
        js("window.onNativeGyro&&window.onNativeGyro(" + z + "," + x + ")");
    }

    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) {
    }

    // ---------------------------------------------------------- ponte com a interface
    public class Bridge {

        @JavascriptInterface
        public boolean isApp() {
            return true;
        }

        @JavascriptInterface
        public boolean hasGyro() {
            return gyro != null;
        }

        @JavascriptInterface
        public void vibrate(int ms) {
            Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            if (v == null) return;
            if (Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createOneShot(Math.max(1, ms), VibrationEffect.DEFAULT_AMPLITUDE));
            else v.vibrate(ms);
        }

        @JavascriptInterface
        public void keepScreenOn(final boolean on) {
            ui.post(() -> {
                if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            });
        }

        @JavascriptInterface
        public void setOrientation(final String mode) {
            ui.post(() -> {
                int o = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED;
                if ("portrait".equals(mode)) o = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT;
                else if ("landscape".equals(mode)) o = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE;
                setRequestedOrientation(o);
            });
        }

        @JavascriptInterface
        public void setImmersive(final boolean on) {
            ui.post(() -> {
                View d = getWindow().getDecorView();
                d.setSystemUiVisibility(on ? (View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE) : 0);
            });
        }

        @JavascriptInterface
        public void setVolumeKeys(boolean on) {
            volumeKeys = on;
        }

        @JavascriptInterface
        public void startGyro() {
            ui.post(() -> {
                if (gyro == null) return;
                gyroOn = true;
                sensors.registerListener(MainActivity.this, gyro, SensorManager.SENSOR_DELAY_GAME);
            });
        }

        @JavascriptInterface
        public void stopGyro() {
            ui.post(() -> {
                gyroOn = false;
                if (sensors != null) sensors.unregisterListener(MainActivity.this);
            });
        }

        @JavascriptInterface
        public void copy(String text) {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm != null) cm.setPrimaryClip(ClipData.newPlainText("Remoto GF", text));
        }

        @JavascriptInterface
        public String getClip() {
            try {
                ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                if (cm != null && cm.hasPrimaryClip() && cm.getPrimaryClip() != null && cm.getPrimaryClip().getItemCount() > 0) {
                    CharSequence cs = cm.getPrimaryClip().getItemAt(0).getText();
                    return cs != null ? cs.toString() : "";
                }
            } catch (Exception ignored) {
            }
            return "";
        }

        @JavascriptInterface
        public void openAppSettings() {
            try {
                Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.fromParts("package", getPackageName(), null));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
            } catch (Exception ignored) {
            }
        }

        @JavascriptInterface
        public void wakeOnLan(String mac, String hostIp) {
            new Thread(() -> {
                try {
                    String clean = mac == null ? "" : mac.replaceAll("[^0-9A-Fa-f]", "");
                    if (clean.length() != 12) return;
                    byte[] macBytes = new byte[6];
                    for (int i = 0; i < 6; i++) {
                        macBytes[i] = (byte) Integer.parseInt(clean.substring(i * 2, i * 2 + 2), 16);
                    }
                    byte[] pkt = new byte[6 + 16 * 6];
                    for (int i = 0; i < 6; i++) pkt[i] = (byte) 0xFF;
                    for (int i = 6; i < pkt.length; i += 6) {
                        System.arraycopy(macBytes, 0, pkt, i, 6);
                    }
                    try (DatagramSocket sock = new DatagramSocket()) {
                        sock.setBroadcast(true);
                        InetAddress bcast = InetAddress.getByName("255.255.255.255");
                        for (int port : new int[]{9, 7, DISCOVERY_PORT}) {
                            sock.send(new DatagramPacket(pkt, pkt.length, bcast, port));
                        }
                        if (hostIp != null && hostIp.contains(".")) {
                            String ipOnly = hostIp.split(":")[0];
                            int lastDot = ipOnly.lastIndexOf('.');
                            if (lastDot > 0) {
                                InetAddress sub = InetAddress.getByName(ipOnly.substring(0, lastDot) + ".255");
                                sock.send(new DatagramPacket(pkt, pkt.length, sub, 9));
                            }
                        }
                    }
                } catch (Exception ignored) {
                }
            }).start();
        }

        @JavascriptInterface
        public void notify(String title, String msg) {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm == null) return;
            Intent open = new Intent(MainActivity.this, MainActivity.class);
            open.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
            PendingIntent pi = PendingIntent.getActivity(MainActivity.this, 0, open,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            Notification.Builder b = Build.VERSION.SDK_INT >= 26
                    ? new Notification.Builder(MainActivity.this, CHANNEL)
                    : new Notification.Builder(MainActivity.this);
            b.setSmallIcon(android.R.drawable.ic_dialog_info)
                    .setContentTitle(title)
                    .setContentText(msg)
                    .setStyle(new Notification.BigTextStyle().bigText(msg))
                    .setAutoCancel(true)
                    .setContentIntent(pi);
            try {
                nm.notify(notifId++, b.build());
            } catch (SecurityException ignored) {
            }
        }

        // ---- progresso dos downloads do PC (notificação com barra)

        private int dlId(String id) {
            return 5000 + ((id == null ? 0 : id.hashCode()) & 0xFFF);
        }

        private PendingIntent openApp() {
            Intent open = new Intent(MainActivity.this, MainActivity.class);
            open.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
            return PendingIntent.getActivity(MainActivity.this, 1, open,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        }

        /** Notificação fixa com barra de progresso (atualizada a cada segundo pelo PC). */
        @JavascriptInterface
        public void progress(String id, String title, String text, int pct, boolean paused) {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm == null) return;
            Notification.Builder b = Build.VERSION.SDK_INT >= 26
                    ? new Notification.Builder(MainActivity.this, CHANNEL_DL)
                    : new Notification.Builder(MainActivity.this);
            b.setSmallIcon(paused ? android.R.drawable.ic_media_pause : android.R.drawable.stat_sys_download)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text))
                    .setProgress(100, Math.max(0, Math.min(100, pct)), false)
                    .setOngoing(true)
                    .setOnlyAlertOnce(true)
                    .setShowWhen(false)
                    .setContentIntent(openApp());
            Notification n = b.build();
            // serviço fixo: segura o app vivo em segundo plano enquanto o PC baixa/renderiza
            if (!ProgressService.running) {
                try {
                    Intent si = new Intent(MainActivity.this, ProgressService.class)
                            .putExtra("id", dlId(id)).putExtra("notif", n);
                    if (Build.VERSION.SDK_INT >= 26) startForegroundService(si);
                    else startService(si);
                } catch (Exception ignored) {
                    // Android não deixou iniciar (app já estava em segundo plano): fica só a notificação
                }
            }
            try {
                nm.notify(dlId(id), n);
            } catch (SecurityException ignored) {
            }
        }

        private void stopProgressService(int nid, boolean keep) {
            if (!ProgressService.running || ProgressService.currentId != nid) return;
            try {
                startService(new Intent(MainActivity.this, ProgressService.class)
                        .setAction(keep ? ProgressService.ACTION_STOP_DETACH : ProgressService.ACTION_STOP_REMOVE));
            } catch (Exception ignored) {
            }
        }

        /** Troca a notificação de progresso por um aviso de concluído/parado/erro (com som). */
        @JavascriptInterface
        public void progressDone(String id, String title, String text) {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm == null) return;
            stopProgressService(dlId(id), true);
            nm.cancel(dlId(id));
            Notification.Builder b = Build.VERSION.SDK_INT >= 26
                    ? new Notification.Builder(MainActivity.this, CHANNEL)
                    : new Notification.Builder(MainActivity.this);
            b.setSmallIcon(android.R.drawable.stat_sys_download_done)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text))
                    .setAutoCancel(true)
                    .setContentIntent(openApp());
            try {
                nm.notify(dlId(id), b.build());
            } catch (SecurityException ignored) {
            }
        }

        @JavascriptInterface
        public void progressClear(String id) {
            stopProgressService(dlId(id), false);
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) nm.cancel(dlId(id));
        }

        /** Procura PCs com o Remoto GF aberto na mesma rede (resposta UDP). */
        @JavascriptInterface
        public void discover() {
            new Thread(() -> {
                WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
                WifiManager.MulticastLock lock = null;
                try {
                    if (wm != null) {
                        lock = wm.createMulticastLock("remotogf");
                        lock.acquire();
                    }
                } catch (Exception ignored) {
                }
                try (DatagramSocket sock = new DatagramSocket()) {
                    sock.setBroadcast(true);
                    sock.setSoTimeout(700);
                    byte[] ask = "REMOTO_GF?".getBytes(StandardCharsets.UTF_8);
                    InetAddress all = InetAddress.getByName("255.255.255.255");
                    long end = System.currentTimeMillis() + 2600;
                    int sent = 0;
                    byte[] buf = new byte[2048];
                    while (System.currentTimeMillis() < end) {
                        if (sent < 3) {
                            sock.send(new DatagramPacket(ask, ask.length, all, DISCOVERY_PORT));
                            sent++;
                        }
                        try {
                            DatagramPacket p = new DatagramPacket(buf, buf.length);
                            sock.receive(p);
                            String txt = new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8);
                            JSONObject j = new JSONObject(txt);
                            j.put("ip", p.getAddress().getHostAddress());
                            js("window.onDiscovered&&window.onDiscovered(" + JSONObject.quote(j.toString()) + ")");
                        } catch (SocketTimeoutException ignored) {
                        } catch (Exception ignored) {
                        }
                    }
                } catch (Exception e) {
                    js("window.onDiscoverError&&window.onDiscoverError(" + JSONObject.quote(String.valueOf(e.getMessage())) + ")");
                } finally {
                    if (lock != null && lock.isHeld()) lock.release();
                    js("window.onDiscoverDone&&window.onDiscoverDone()");
                }
            }).start();
        }

        /** Abre o reconhecimento de voz do Android; o texto volta em window.onDictation. */
        @JavascriptInterface
        public void dictate() {
            ui.post(() -> {
                Intent i = new Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
                i.putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                        android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
                i.putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE, "pt-BR");
                i.putExtra(android.speech.RecognizerIntent.EXTRA_PROMPT, "Fale o que digitar no PC");
                try {
                    startActivityForResult(i, REQ_SPEECH);
                } catch (ActivityNotFoundException e) {
                    js("window.onDictationError&&window.onDictationError('Este celular não tem reconhecimento de voz (app Google).')");
                }
            });
        }

        // ---- Compartilhar > Enviar ao PC

        /** Devolve (uma vez) o que foi compartilhado: {"text": "...", "files": ["nome", ...]} */
        @JavascriptInterface
        public String takeShared() {
            String s = pendingShareJson;
            pendingShareJson = null;
            return s == null ? "" : s;
        }

        /** Envia ao PC os arquivos compartilhados (mesma rota /api/upload da aba Arquivos). */
        @JavascriptInterface
        public void uploadShared(final String base, final String token, final String dev) {
            final List<Uri> uris = new ArrayList<>(sharedUris);
            sharedUris.clear();
            new Thread(() -> {
                int ok = 0;
                String err = "";
                for (int i = 0; i < uris.size(); i++) {
                    Uri u = uris.get(i);
                    String name = displayName(u);
                    js("window.onShareProgress&&window.onShareProgress(" + i + "," + uris.size() + "," + JSONObject.quote(name) + ")");
                    HttpURLConnection c = null;
                    try {
                        String b = "----gfshare" + System.currentTimeMillis();
                        URL url = new URL(base + "/api/upload?t=" + URLEncoder.encode(token, "UTF-8")
                                + "&n=" + URLEncoder.encode(dev, "UTF-8"));
                        c = (HttpURLConnection) url.openConnection();
                        c.setDoOutput(true);
                        c.setRequestMethod("POST");
                        c.setConnectTimeout(8000);
                        c.setReadTimeout(120000);
                        c.setChunkedStreamingMode(256 * 1024);
                        c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + b);
                        try (OutputStream out = c.getOutputStream();
                             InputStream in = getContentResolver().openInputStream(u)) {
                            if (in == null) throw new Exception("não consegui abrir " + name);
                            out.write(("--" + b + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\""
                                    + name.replace("\"", "_") + "\"\r\nContent-Type: application/octet-stream\r\n\r\n")
                                    .getBytes("UTF-8"));
                            byte[] buf = new byte[256 * 1024];
                            int n;
                            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                            out.write(("\r\n--" + b + "--\r\n").getBytes("UTF-8"));
                        }
                        int code = c.getResponseCode();
                        if (code >= 200 && code < 300) ok++;
                        else err = "o PC respondeu " + code;
                    } catch (Exception e) {
                        err = String.valueOf(e.getMessage());
                    } finally {
                        if (c != null) c.disconnect();
                    }
                }
                js("window.onShareDone&&window.onShareDone(" + ok + "," + uris.size() + "," + JSONObject.quote(err) + ")");
            }).start();
        }

        @JavascriptInterface
        public String version() {
            try {
                return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
            } catch (Exception e) {
                return "?";
            }
        }

        // ---- modo "ser controlado pelo PC"

        @JavascriptInterface
        public boolean controlRunning() {
            return ScreenService.running;
        }

        /** True quando o usuário já ativou o serviço de acessibilidade do Remoto GF. */
        @JavascriptInterface
        public boolean accessibilityOn() {
            String want = getPackageName() + "/" + ControlService.class.getName();
            String enabled = Settings.Secure.getString(getContentResolver(),
                    Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            if (TextUtils.isEmpty(enabled)) return false;
            TextUtils.SimpleStringSplitter sp = new TextUtils.SimpleStringSplitter(':');
            sp.setString(enabled);
            while (sp.hasNext()) {
                if (sp.next().equalsIgnoreCase(want)) return true;
            }
            return false;
        }

        @JavascriptInterface
        public void openAccessibility() {
            try {
                startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            } catch (Exception ignored) {
            }
        }

        /** Pede a confirmação de projeção de tela; ao confirmar, onActivityResult inicia a sessão. */
        @JavascriptInterface
        public void startBeControlled(String host, String token, String name) {
            pendingHost = host;
            pendingToken = token;
            pendingName = name;
            ui.post(() -> {
                MediaProjectionManager mpm = (MediaProjectionManager) getSystemService(Context.MEDIA_PROJECTION_SERVICE);
                startActivityForResult(mpm.createScreenCaptureIntent(), REQ_PROJECTION);
            });
        }

        @JavascriptInterface
        public void stopBeControlled() {
            startService(new Intent(MainActivity.this, ScreenService.class).setAction(ScreenService.ACTION_STOP));
        }

        @JavascriptInterface
        public void downloadFile(String url, String filename) {
            try {
                DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                if (dm == null) return;
                String safe = (filename == null || filename.trim().isEmpty()) ? "arquivo" : filename.trim();
                DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url))
                        .setTitle(safe)
                        .setDescription("Baixando do PC pelo Remoto GF")
                        .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                        .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, safe);
                dm.enqueue(req);
            } catch (Exception ignored) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                } catch (Exception ignored2) {
                }
            }
        }
    }
}
