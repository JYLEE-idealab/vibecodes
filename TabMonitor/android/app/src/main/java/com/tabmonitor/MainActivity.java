package com.tabmonitor;

import android.app.Activity;
import android.app.Dialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.StrictMode;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

public class MainActivity extends Activity implements SurfaceHolder.Callback, View.OnTouchListener {
    private static final String TAG = "TabMonitor";
    private static final String HOST = "127.0.0.1";
    private static final int VIDEO_PORT = 12347;
    private static final int TOUCH_PORT = 12348;
    private static final int CTRL_PORT = 12355;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private FrameLayout rootLayout;
    private SurfaceView surfaceView;
    private SurfaceHolder surfaceHolder;
    private LinearLayout dashboardOverlay;

    // Dashboard UI widgets
    private TextView badgeStatus;
    private TextView lblPcStatus;
    private TextView lblPcSub;
    private TextView lblDisplayRes;
    private TextView lblDisplaySub;
    private TextView btnRes1080;
    private TextView btnRes720;
    private TextView lblFps;
    private TextView lblBitrate;
    private TextView lblTouchState;
    private TextView btnStartMonitor;

    // Log dialog & history
    private final StringBuilder logHistory = new StringBuilder();
    private Dialog logDialog;
    private TextView logDialogText;
    private ScrollView logScrollView;

    // State
    private volatile boolean surfaceReady = false;
    private volatile boolean wantStreaming = false;
    private volatile boolean isVideoConnected = false;
    private volatile String selectedRes = "1920x1080";
    private Thread videoThread;
    private Thread touchThread;
    private Socket activeVideoSocket;
    private Socket activeTouchSocket;
    private final LinkedBlockingQueue<byte[]> touchQueue = new LinkedBlockingQueue<>(512);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        StrictMode.setThreadPolicy(new StrictMode.ThreadPolicy.Builder().permitAll().build());
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        hideSystemUI();

        buildUI();

        // Check if PC triggered this launch directly ("from_pc" intent extra)
        boolean fromPc = getIntent() != null && getIntent().getBooleanExtra("from_pc", false);
        appendLog("TabMonitor Studio 태블릿 앱이 준비되었습니다.");
        if (fromPc) {
            appendLog("PC에서 보조모니터 시작 요청을 수신했습니다.");
            startMonitorSession(false);
        } else {
            // Proactively probe if PC is already streaming
            new Thread(this::probePcStatusOnce).start();
        }
    }

    @Override
    protected void onNewIntent(android.content.Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (intent != null && intent.getBooleanExtra("from_pc", false)) {
            appendLog("PC에서 보조모니터 자동 연결 요청을 수신했습니다.");
            startMonitorSession(false);
        }
    }

    private int dp(int v) {
        return Math.round(TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()));
    }

    private GradientDrawable roundedBox(String fillHex, String strokeHex, int radiusDp, int strokeDp) {
        GradientDrawable gd = new GradientDrawable();
        gd.setColor(Color.parseColor(fillHex));
        gd.setCornerRadius(dp(radiusDp));
        if (strokeHex != null && strokeDp > 0) {
            gd.setStroke(dp(strokeDp), Color.parseColor(strokeHex));
        }
        return gd;
    }

    private void buildUI() {
        rootLayout = new FrameLayout(this);
        rootLayout.setBackgroundColor(Color.parseColor("#0B0F17"));

        // 1. 16:9 SurfaceView for video stream (underneath dashboard)
        surfaceView = new SurfaceView(this);
        surfaceView.getHolder().addCallback(this);
        surfaceView.setOnTouchListener(this);

        FrameLayout.LayoutParams svParams = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
                Gravity.CENTER
        );
        rootLayout.addView(surfaceView, svParams);

        rootLayout.addOnLayoutChangeListener((v, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom) -> {
            int parentW = right - left;
            int parentH = bottom - top;
            if (parentW <= 0 || parentH <= 0) return;

            int targetW;
            int targetH;
            if (parentW * 9 <= parentH * 16) {
                targetW = parentW;
                targetH = Math.round(parentW * 9.0f / 16.0f);
            } else {
                targetH = parentH;
                targetW = Math.round(parentH * 16.0f / 9.0f);
            }

            FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) surfaceView.getLayoutParams();
            if (lp.width != targetW || lp.height != targetH) {
                lp.width = targetW;
                lp.height = targetH;
                lp.gravity = Gravity.CENTER;
                surfaceView.post(() -> surfaceView.setLayoutParams(lp));
            }
        });

        // 2. Modern Dark Dashboard Overlay (Matching PC TabMonitor Studio UI)
        dashboardOverlay = new LinearLayout(this);
        dashboardOverlay.setOrientation(LinearLayout.VERTICAL);
        dashboardOverlay.setGravity(Gravity.CENTER);
        dashboardOverlay.setBackgroundColor(Color.parseColor("#0B0F17"));
        dashboardOverlay.setClickable(true);

        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams containerLp = new LinearLayout.LayoutParams(dp(680), LinearLayout.LayoutParams.WRAP_CONTENT);
        container.setLayoutParams(containerLp);

        // --- Header Card ---
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setBackground(roundedBox("#131B2E", "#23304C", 16, 1));
        header.setPadding(dp(22), dp(16), dp(22), dp(16));

        LinearLayout headerLeft = new LinearLayout(this);
        headerLeft.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams hlLp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        headerLeft.setLayoutParams(hlLp);

        TextView title = new TextView(this);
        title.setText("TabMonitor Studio");
        title.setTextColor(Color.parseColor("#F8FAFC"));
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        headerLeft.addView(title);

        TextView subtitle = new TextView(this);
        subtitle.setText("Galaxy Tab S7 FE • USB 초저지연 NVENC 그래픽 & 터치 보조모니터");
        subtitle.setTextColor(Color.parseColor("#94A3B8"));
        subtitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        headerLeft.addView(subtitle);

        header.addView(headerLeft);

        badgeStatus = new TextView(this);
        badgeStatus.setText(" ● 대기 중 ");
        badgeStatus.setTextColor(Color.parseColor("#94A3B8"));
        badgeStatus.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        badgeStatus.setTypeface(Typeface.DEFAULT_BOLD);
        badgeStatus.setBackground(roundedBox("#1E293B", null, 16, 0));
        badgeStatus.setPadding(dp(14), dp(7), dp(14), dp(7));
        header.addView(badgeStatus);

        TextView btnLog = new TextView(this);
        btnLog.setText("📋 로그 창 열기");
        btnLog.setTextColor(Color.parseColor("#E2E8F0"));
        btnLog.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        btnLog.setTypeface(Typeface.DEFAULT_BOLD);
        btnLog.setBackground(roundedBox("#1E293B", "#334155", 16, 1));
        btnLog.setPadding(dp(14), dp(7), dp(14), dp(7));
        LinearLayout.LayoutParams logBtnLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        logBtnLp.leftMargin = dp(10);
        btnLog.setLayoutParams(logBtnLp);
        btnLog.setOnClickListener(v -> showLogWindow());
        header.addView(btnLog);

        container.addView(header);

        // --- Status Cards Row (2 Columns) ---
        LinearLayout cardsRow = new LinearLayout(this);
        cardsRow.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams crLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        crLp.topMargin = dp(12);
        cardsRow.setLayoutParams(crLp);

        // Card 1: USB PC Connection
        LinearLayout cardPc = new LinearLayout(this);
        cardPc.setOrientation(LinearLayout.VERTICAL);
        cardPc.setBackground(roundedBox("#151C28", "#263042", 14, 1));
        cardPc.setPadding(dp(18), dp(14), dp(18), dp(14));
        LinearLayout.LayoutParams c1Lp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        c1Lp.rightMargin = dp(6);
        cardPc.setLayoutParams(c1Lp);

        TextView c1Title = new TextView(this);
        c1Title.setText("💻 연결된 Windows PC (USB)");
        c1Title.setTextColor(Color.parseColor("#64748B"));
        c1Title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        c1Title.setTypeface(Typeface.DEFAULT_BOLD);
        cardPc.addView(c1Title);

        lblPcStatus = new TextView(this);
        lblPcStatus.setText("USB 케이블 연결 준비됨");
        lblPcStatus.setTextColor(Color.parseColor("#34D399"));
        lblPcStatus.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        lblPcStatus.setTypeface(Typeface.DEFAULT_BOLD);
        lblPcStatus.setPadding(0, dp(4), 0, dp(2));
        cardPc.addView(lblPcStatus);

        lblPcSub = new TextView(this);
        lblPcSub.setText("시작 버튼을 누르면 PC 프로그램이 자동 실행됩니다");
        lblPcSub.setTextColor(Color.parseColor("#94A3B8"));
        lblPcSub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        cardPc.addView(lblPcSub);

        cardsRow.addView(cardPc);

        // Card 2: Virtual Display Info
        LinearLayout cardMon = new LinearLayout(this);
        cardMon.setOrientation(LinearLayout.VERTICAL);
        cardMon.setBackground(roundedBox("#151C28", "#263042", 14, 1));
        cardMon.setPadding(dp(18), dp(14), dp(18), dp(14));
        LinearLayout.LayoutParams c2Lp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        c2Lp.leftMargin = dp(6);
        cardMon.setLayoutParams(c2Lp);

        TextView c2Title = new TextView(this);
        c2Title.setText("🖥️ 보조 디스플레이 출력");
        c2Title.setTextColor(Color.parseColor("#64748B"));
        c2Title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        c2Title.setTypeface(Typeface.DEFAULT_BOLD);
        cardMon.addView(c2Title);

        lblDisplayRes = new TextView(this);
        lblDisplayRes.setText("1920 × 1080 @ 60Hz");
        lblDisplayRes.setTextColor(Color.parseColor("#38BDF8"));
        lblDisplayRes.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        lblDisplayRes.setTypeface(Typeface.DEFAULT_BOLD);
        lblDisplayRes.setPadding(0, dp(4), 0, dp(2));
        cardMon.addView(lblDisplayRes);

        lblDisplaySub = new TextView(this);
        lblDisplaySub.setText("16:9 고정 화면비 • 자동 회전 지원");
        lblDisplaySub.setTextColor(Color.parseColor("#94A3B8"));
        lblDisplaySub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        cardMon.addView(lblDisplaySub);

        cardsRow.addView(cardMon);
        container.addView(cardsRow);

        // --- Resolution Selector Card ---
        LinearLayout resCard = new LinearLayout(this);
        resCard.setOrientation(LinearLayout.VERTICAL);
        resCard.setBackground(roundedBox("#151C28", "#263042", 14, 1));
        resCard.setPadding(dp(18), dp(14), dp(18), dp(14));
        LinearLayout.LayoutParams rcLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        rcLp.topMargin = dp(12);
        resCard.setLayoutParams(rcLp);

        LinearLayout resHeader = new LinearLayout(this);
        resHeader.setOrientation(LinearLayout.HORIZONTAL);
        TextView resTitle = new TextView(this);
        resTitle.setText("⚡ 해상도 설정 (PC 가상 모니터 즉시 연동)");
        resTitle.setTextColor(Color.parseColor("#F1F5F9"));
        resTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        resTitle.setTypeface(Typeface.DEFAULT_BOLD);
        resHeader.addView(resTitle);
        resCard.addView(resHeader);

        LinearLayout segRow = new LinearLayout(this);
        segRow.setOrientation(LinearLayout.HORIZONTAL);
        segRow.setBackground(roundedBox("#0B0F17", null, 10, 0));
        segRow.setPadding(dp(4), dp(4), dp(4), dp(4));
        LinearLayout.LayoutParams srLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        srLp.topMargin = dp(10);
        segRow.setLayoutParams(srLp);

        btnRes1080 = new TextView(this);
        btnRes1080.setText("1920 × 1080 (FHD 60p)");
        btnRes1080.setGravity(Gravity.CENTER);
        btnRes1080.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        btnRes1080.setTypeface(Typeface.DEFAULT_BOLD);
        btnRes1080.setPadding(0, dp(10), 0, dp(10));
        btnRes1080.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        btnRes1080.setOnClickListener(v -> selectResolution("1920x1080"));

        btnRes720 = new TextView(this);
        btnRes720.setText("1280 × 720 (HD 60p)");
        btnRes720.setGravity(Gravity.CENTER);
        btnRes720.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        btnRes720.setTypeface(Typeface.DEFAULT_BOLD);
        btnRes720.setPadding(0, dp(10), 0, dp(10));
        btnRes720.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        btnRes720.setOnClickListener(v -> selectResolution("1280x720"));

        segRow.addView(btnRes1080);
        segRow.addView(btnRes720);
        resCard.addView(segRow);
        container.addView(resCard);
        updateResSelectionUI();

        // --- Telemetry Row (3 Boxes) ---
        LinearLayout metricsRow = new LinearLayout(this);
        metricsRow.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams mrLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        mrLp.topMargin = dp(12);
        metricsRow.setLayoutParams(mrLp);

        lblFps = createMetricBox(metricsRow, "실시간 프레임", "60.0 FPS 목표", 0, 6);
        lblBitrate = createMetricBox(metricsRow, "하드웨어 디코더", "MediaCodec ULL", 6, 6);
        lblTouchState = createMetricBox(metricsRow, "터치 ➔ 마우스", "1:1 절대좌표", 6, 0);

        container.addView(metricsRow);

        // --- Primary Start Button ---
        btnStartMonitor = new TextView(this);
        btnStartMonitor.setText("▶  보조모니터 시작  (PC 프로그램 자동 실행 및 연결)");
        btnStartMonitor.setTextColor(Color.WHITE);
        btnStartMonitor.setGravity(Gravity.CENTER);
        btnStartMonitor.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        btnStartMonitor.setTypeface(Typeface.DEFAULT_BOLD);
        btnStartMonitor.setBackground(roundedBox("#10B981", null, 14, 0));
        btnStartMonitor.setPadding(dp(20), dp(16), dp(20), dp(16));
        LinearLayout.LayoutParams btnLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        btnLp.topMargin = dp(16);
        btnStartMonitor.setLayoutParams(btnLp);
        btnStartMonitor.setOnClickListener(v -> startMonitorSession(true));
        container.addView(btnStartMonitor);

        dashboardOverlay.addView(container);
        rootLayout.addView(dashboardOverlay, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        setContentView(rootLayout);
    }

    private TextView createMetricBox(LinearLayout parent, String title, String val, int leftMarginDp, int rightMarginDp) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackground(roundedBox("#151C28", "#263042", 12, 1));
        box.setPadding(dp(12), dp(12), dp(12), dp(12));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        lp.leftMargin = dp(leftMarginDp);
        lp.rightMargin = dp(rightMarginDp);
        box.setLayoutParams(lp);

        TextView t = new TextView(this);
        t.setText(title);
        t.setTextColor(Color.parseColor("#64748B"));
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        t.setGravity(Gravity.CENTER);
        box.addView(t);

        TextView v = new TextView(this);
        v.setText(val);
        v.setTextColor(Color.parseColor("#F8FAFC"));
        v.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        v.setTypeface(Typeface.DEFAULT_BOLD);
        v.setGravity(Gravity.CENTER);
        v.setPadding(0, dp(3), 0, 0);
        box.addView(v);

        parent.addView(box);
        return v;
    }

    private void updateResSelectionUI() {
        if ("1280x720".equals(selectedRes)) {
            btnRes1080.setBackground(roundedBox("#0B0F17", null, 8, 0));
            btnRes1080.setTextColor(Color.parseColor("#94A3B8"));
            btnRes720.setBackground(roundedBox("#2563EB", null, 8, 0));
            btnRes720.setTextColor(Color.WHITE);
            lblDisplayRes.setText("1280 × 720 @ 60Hz");
        } else {
            btnRes1080.setBackground(roundedBox("#2563EB", null, 8, 0));
            btnRes1080.setTextColor(Color.WHITE);
            btnRes720.setBackground(roundedBox("#0B0F17", null, 8, 0));
            btnRes720.setTextColor(Color.parseColor("#94A3B8"));
            lblDisplayRes.setText("1920 × 1080 @ 60Hz");
        }
    }

    private void selectResolution(String res) {
        selectedRes = res;
        updateResSelectionUI();
        appendLog("해상도 선택 변경: " + res + " @ 60Hz");
        new Thread(() -> sendControlCommand("RES_" + res)).start();
    }

    private void showLogWindow() {
        if (logDialog != null && logDialog.isShowing()) {
            logDialog.dismiss();
            return;
        }
        logDialog = new Dialog(this);
        logDialog.requestWindowFeature(Window.FEATURE_NO_TITLE);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackground(roundedBox("#0B0F17", "#23304C", 16, 1));
        root.setPadding(dp(18), dp(16), dp(18), dp(16));

        LinearLayout topBar = new LinearLayout(this);
        topBar.setOrientation(LinearLayout.HORIZONTAL);
        topBar.setGravity(Gravity.CENTER_VERTICAL);
        topBar.setBackground(roundedBox("#131B2E", "#23304C", 10, 1));
        topBar.setPadding(dp(14), dp(10), dp(14), dp(10));

        TextView title = new TextView(this);
        title.setText("📋 실시간 시스템 로그");
        title.setTextColor(Color.parseColor("#F8FAFC"));
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        topBar.addView(title);

        TextView btnClear = new TextView(this);
        btnClear.setText("로그 지우기");
        btnClear.setTextColor(Color.parseColor("#E2E8F0"));
        btnClear.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        btnClear.setBackground(roundedBox("#1E293B", null, 8, 0));
        btnClear.setPadding(dp(12), dp(6), dp(12), dp(6));
        btnClear.setOnClickListener(v -> {
            synchronized (logHistory) {
                logHistory.setLength(0);
            }
            if (logDialogText != null) logDialogText.setText("");
        });
        topBar.addView(btnClear);

        TextView btnClose = new TextView(this);
        btnClose.setText("닫기");
        btnClose.setTextColor(Color.WHITE);
        btnClose.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        btnClose.setBackground(roundedBox("#2563EB", null, 8, 0));
        btnClose.setPadding(dp(14), dp(6), dp(14), dp(6));
        LinearLayout.LayoutParams closeLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        closeLp.leftMargin = dp(8);
        btnClose.setLayoutParams(closeLp);
        btnClose.setOnClickListener(v -> logDialog.dismiss());
        topBar.addView(btnClose);

        root.addView(topBar);

        logScrollView = new ScrollView(this);
        logScrollView.setBackground(roundedBox("#111622", "#1E293B", 10, 1));
        LinearLayout.LayoutParams svLp = new LinearLayout.LayoutParams(dp(540), dp(260));
        svLp.topMargin = dp(10);
        logScrollView.setLayoutParams(svLp);

        logDialogText = new TextView(this);
        logDialogText.setTypeface(Typeface.SANS_SERIF);
        logDialogText.setTextColor(Color.parseColor("#E2E8F0"));
        logDialogText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        logDialogText.setPadding(dp(14), dp(12), dp(14), dp(12));
        synchronized (logHistory) {
            logDialogText.setText(logHistory.toString());
        }
        logScrollView.addView(logDialogText);
        root.addView(logScrollView);

        logDialog.setContentView(root);
        if (logDialog.getWindow() != null) {
            logDialog.getWindow().setBackgroundDrawableResource(android.R.color.transparent);
        }
        logDialog.show();
        logScrollView.post(() -> logScrollView.fullScroll(View.FOCUS_DOWN));
    }

    private void appendLog(String msg) {
        String ts = new SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(new Date());
        String line = "[" + ts + "]  " + msg + "\n";
        synchronized (logHistory) {
            logHistory.append(line);
        }
        mainHandler.post(() -> {
            if (logDialog != null && logDialog.isShowing() && logDialogText != null) {
                logDialogText.append(line);
                if (logScrollView != null) {
                    logScrollView.fullScroll(View.FOCUS_DOWN);
                }
            }
        });
    }

    private void probePcStatusOnce() {
        try {
            Socket s = new Socket();
            s.connect(new InetSocketAddress(HOST, CTRL_PORT), 400);
            OutputStream out = s.getOutputStream();
            out.write("PING".getBytes("UTF-8"));
            out.flush();
            s.close();
            mainHandler.post(() -> {
                lblPcStatus.setText("Windows PC 연결됨 (USB 초고속 통신)");
                lblPcStatus.setTextColor(Color.parseColor("#34D399"));
                badgeStatus.setText(" ● 연결 준비됨 ");
                badgeStatus.setTextColor(Color.parseColor("#60A5FA"));
                badgeStatus.setBackground(roundedBox("#1E3A8A", null, 16, 0));
            });
            appendLog("PC의 TabMonitor 백그라운드 서비스와 USB 연결이 확인되었습니다.");
        } catch (Throwable ignored) {}
    }

    private void sendControlCommand(String cmd) {
        try {
            Socket s = new Socket();
            s.connect(new InetSocketAddress(HOST, CTRL_PORT), 600);
            OutputStream out = s.getOutputStream();
            out.write(cmd.getBytes("UTF-8"));
            out.flush();
            s.close();
        } catch (Throwable ignored) {}
    }

    private void startMonitorSession(boolean notifyPcToLaunchGui) {
        Log.i(TAG, "TABMONITOR_REQUEST_START_" + selectedRes);
        appendLog("PC 보조모니터 연결 및 스트리밍을 시작합니다 (" + selectedRes + ")...");

        btnStartMonitor.setText("PC 프로그램 실행 및 영상 스트림 연결 중...");
        btnStartMonitor.setBackground(roundedBox("#2563EB", null, 14, 0));
        badgeStatus.setText(" ● 연결 중... ");
        badgeStatus.setTextColor(Color.parseColor("#60A5FA"));

        wantStreaming = true;
        if (notifyPcToLaunchGui) {
            new Thread(() -> sendControlCommand("AUTO_START_" + selectedRes)).start();
        }
        ensureStreamThreadsRunning();
    }

    @Override
    public void onBackPressed() {
        if (wantStreaming && dashboardOverlay.getVisibility() != View.VISIBLE) {
            appendLog("스트리밍 화면에서 대시보드 화면으로 전환했습니다.");
            wantStreaming = false;
            stopStreams();
            dashboardOverlay.setVisibility(View.VISIBLE);
            btnStartMonitor.setText("▶  보조모니터 시작  (PC 프로그램 자동 실행 및 연결)");
            btnStartMonitor.setBackground(roundedBox("#10B981", null, 14, 0));
            badgeStatus.setText(" ● 연결 준비됨 ");
            badgeStatus.setTextColor(Color.parseColor("#60A5FA"));
            new Thread(() -> sendControlCommand("STOP_FROM_TABLET")).start();
        } else {
            super.onBackPressed();
        }
    }

    private void hideSystemUI() {
        View decorView = getWindow().getDecorView();
        decorView.setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_FULLSCREEN);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemUI();
        }
    }

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        surfaceHolder = holder;
        surfaceReady = true;
        if (wantStreaming) {
            ensureStreamThreadsRunning();
        }
    }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {}

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) {
        surfaceReady = false;
        stopStreams();
    }

    private synchronized void ensureStreamThreadsRunning() {
        if (!surfaceReady || !wantStreaming) return;
        if (videoThread == null || !videoThread.isAlive()) {
            videoThread = new Thread(this::runVideoReceiver, "VideoReceiver");
            videoThread.start();
        }
        if (touchThread == null || !touchThread.isAlive()) {
            touchThread = new Thread(this::runTouchSender, "TouchSender");
            touchThread.start();
        }
    }

    private synchronized void stopStreams() {
        isVideoConnected = false;
        try {
            if (activeVideoSocket != null) activeVideoSocket.close();
        } catch (Throwable ignored) {}
        try {
            if (activeTouchSocket != null) activeTouchSocket.close();
        } catch (Throwable ignored) {}
        try {
            if (videoThread != null) videoThread.interrupt();
            if (touchThread != null) touchThread.interrupt();
        } catch (Throwable ignored) {}
    }

    private void runVideoReceiver() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
        byte[] frameBuffer = new byte[2 * 1024 * 1024];

        while (wantStreaming && surfaceReady) {
            Socket socket = null;
            MediaCodec codec = null;
            try {
                socket = new Socket();
                socket.connect(new InetSocketAddress(HOST, VIDEO_PORT), 1000);
                socket.setTcpNoDelay(true);
                socket.setReceiveBufferSize(2 * 1024 * 1024);
                activeVideoSocket = socket;
                DataInputStream in = new DataInputStream(new BufferedInputStream(socket.getInputStream(), 262144));

                MediaFormat format = MediaFormat.createVideoFormat("video/avc", 1920, 1080);
                format.setInteger(MediaFormat.KEY_MAX_WIDTH, 1920);
                format.setInteger(MediaFormat.KEY_MAX_HEIGHT, 1080);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
                }
                format.setInteger(MediaFormat.KEY_PRIORITY, 0);
                codec = MediaCodec.createDecoderByType("video/avc");
                codec.configure(format, surfaceHolder.getSurface(), null, 0);
                codec.start();

                MediaCodec.BufferInfo bufferInfo = new MediaCodec.BufferInfo();
                long frameIndex = 0;

                while (wantStreaming && surfaceReady) {
                    int packetLen = in.readInt();
                    if (packetLen <= 0 || packetLen > frameBuffer.length) {
                        break;
                    }
                    in.readFully(frameBuffer, 0, packetLen);

                    if (frameIndex == 0) {
                        isVideoConnected = true;
                        appendLog("PC 영상 스트림 수신 시작! 전체화면 모드로 전환합니다.");
                        mainHandler.post(() -> {
                            dashboardOverlay.setVisibility(View.GONE);
                            hideSystemUI();
                        });
                    }

                    int inIndex = codec.dequeueInputBuffer(10000);
                    if (inIndex >= 0) {
                        ByteBuffer inputBuffer = codec.getInputBuffer(inIndex);
                        if (inputBuffer != null) {
                            inputBuffer.clear();
                            inputBuffer.put(frameBuffer, 0, packetLen);
                            long pts = (frameIndex * 1000000L) / 60L;
                            codec.queueInputBuffer(inIndex, 0, packetLen, pts, 0);
                            frameIndex++;
                        }
                    }

                    int outIndex = codec.dequeueOutputBuffer(bufferInfo, 0);
                    while (outIndex >= 0) {
                        codec.releaseOutputBuffer(outIndex, true);
                        outIndex = codec.dequeueOutputBuffer(bufferInfo, 0);
                    }
                }
            } catch (Throwable e) {
                // Retry silently while waiting for PC server to spin up
            } finally {
                isVideoConnected = false;
                if (codec != null) {
                    try {
                        codec.stop();
                        codec.release();
                    } catch (Throwable ignored) {}
                }
                if (socket != null) {
                    try {
                        socket.close();
                    } catch (Throwable ignored) {}
                }
                try {
                    Thread.sleep(350);
                } catch (InterruptedException ignored) {}
            }
        }
    }

    private void runTouchSender() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
        while (wantStreaming && surfaceReady) {
            Socket socket = null;
            try {
                socket = new Socket();
                socket.connect(new InetSocketAddress(HOST, TOUCH_PORT), 1000);
                socket.setTcpNoDelay(true);
                activeTouchSocket = socket;
                OutputStream out = socket.getOutputStream();

                touchQueue.clear();
                while (wantStreaming && surfaceReady && !socket.isClosed()) {
                    byte[] pkt = touchQueue.poll(200, TimeUnit.MILLISECONDS);
                    if (pkt != null) {
                        out.write(pkt);
                        while ((pkt = touchQueue.poll()) != null) {
                            out.write(pkt);
                        }
                        out.flush();
                    }
                }
            } catch (Throwable ignored) {
            } finally {
                if (socket != null) {
                    try {
                        socket.close();
                    } catch (Throwable ignored) {}
                }
                try {
                    Thread.sleep(350);
                } catch (InterruptedException ignored) {}
            }
        }
    }

    @Override
    public boolean onTouch(View v, MotionEvent event) {
        if (!isVideoConnected) return false;
        try {
            int action = event.getActionMasked();
            byte type;
            if (action == MotionEvent.ACTION_DOWN) {
                type = 0;
            } else if (action == MotionEvent.ACTION_MOVE) {
                type = 1;
            } else if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
                type = 2;
            } else {
                return true;
            }

            int w = v.getWidth();
            int h = v.getHeight();
            if (w <= 0 || h <= 0) return true;

            float normX = Math.max(0.0f, Math.min(1.0f, event.getX() / (float) w));
            float normY = Math.max(0.0f, Math.min(1.0f, event.getY() / (float) h));

            byte[] pkt = new byte[9];
            ByteBuffer bb = ByteBuffer.wrap(pkt);
            bb.put(type);
            bb.putFloat(normX);
            bb.putFloat(normY);

            if (!touchQueue.offer(pkt)) {
                touchQueue.poll();
                touchQueue.offer(pkt);
            }
        } catch (Throwable ignored) {
        }
        return true;
    }
}
