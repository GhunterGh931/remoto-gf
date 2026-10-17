package br.gf.remoto;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.os.Bundle;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

/**
 * Serviço de acessibilidade que executa, neste celular, os toques que o PC pareado envia
 * durante uma sessão de controle iniciada pelo próprio usuário no Remoto GF.
 *
 * Fica inativo até o usuário iniciar a sessão no app; enquanto ela dura, uma notificação
 * fixa avisa que o PC pode ver e controlar o aparelho, com um botão para encerrar.
 */
public class ControlService extends AccessibilityService {

    static ControlService instance;

    @Override
    public void onServiceConnected() {
        instance = this;
    }

    @Override
    public void onDestroy() {
        if (instance == this) instance = null;
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
    }

    @Override
    public void onInterrupt() {
    }

    static boolean ready() {
        return instance != null;
    }

    // ---- ações pedidas pelo PC, só repassadas quando há sessão ativa (ScreenService liga/desliga)

    void tap(float x, float y) {
        gesture(x, y, x, y, 40);
    }

    void longPress(float x, float y) {
        gesture(x, y, x, y, 600);
    }

    void swipe(float x1, float y1, float x2, float y2, int ms) {
        gesture(x1, y1, x2, y2, Math.max(40, ms));
    }

    private void gesture(float x1, float y1, float x2, float y2, int ms) {
        Path p = new Path();
        p.moveTo(x1, y1);
        if (x1 != x2 || y1 != y2) p.lineTo(x2, y2);
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(p, 0, ms))
                .build();
        dispatchGesture(g, null, null);
    }

    void global(String which) {
        int a;
        switch (which) {
            case "back": a = GLOBAL_ACTION_BACK; break;
            case "home": a = GLOBAL_ACTION_HOME; break;
            case "recents": a = GLOBAL_ACTION_RECENTS; break;
            case "notifications": a = GLOBAL_ACTION_NOTIFICATIONS; break;
            case "lock":
                if (android.os.Build.VERSION.SDK_INT >= 28) a = GLOBAL_ACTION_LOCK_SCREEN;
                else return;
                break;
            case "screenshot":
                if (android.os.Build.VERSION.SDK_INT >= 28) a = GLOBAL_ACTION_TAKE_SCREENSHOT;
                else return;
                break;
            default: return;
        }
        performGlobalAction(a);
    }

    /** Escreve texto no campo em foco (ex.: caixa de mensagem do WhatsApp). */
    void type(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return;
        AccessibilityNodeInfo node = findFocusedEditable(root);
        if (node == null) node = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (node == null) return;
        CharSequence old = node.getText();
        String base = old != null ? old.toString() : "";
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, base + text);
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
    }

    void backspace() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return;
        AccessibilityNodeInfo node = findFocusedEditable(root);
        if (node == null) node = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (node == null) return;
        CharSequence old = node.getText();
        if (old == null || old.length() == 0) return;
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                old.subSequence(0, old.length() - 1));
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
    }

    private AccessibilityNodeInfo findFocusedEditable(AccessibilityNodeInfo n) {
        if (n == null) return null;
        if (n.isEditable() && n.isFocused()) return n;
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo r = findFocusedEditable(n.getChild(i));
            if (r != null) return r;
        }
        return null;
    }
}
