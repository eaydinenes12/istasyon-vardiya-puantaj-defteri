package tr.vardiyapuantaj.mobile;

import android.app.Activity;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.net.URI;
import java.util.Locale;
import java.util.Objects;

public final class MainActivity extends Activity {
    private static final String PREFERENCES = "connection";
    private String origin = "";
    private WebView browser;
    private LinearLayout container;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        origin = getSharedPreferences(PREFERENCES, MODE_PRIVATE).getString("origin", "");
        if (origin.isEmpty()) connectionScreen(""); else connect();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private LinearLayout frame() {
        if (browser != null) {
            ((LinearLayout) browser.getParent()).removeView(browser);
            browser.destroy();
            browser = null;
        }
        container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setBackgroundColor(Color.rgb(242, 246, 243));
        container.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        setContentView(container);
        container.requestApplyInsets();
        return container;
    }

    private void connectionScreen(String message) {
        LinearLayout outer = frame();
        LinearLayout fields = new LinearLayout(this);
        fields.setOrientation(LinearLayout.VERTICAL);
        fields.setPadding(dp(20), dp(24), dp(20), dp(16));
        outer.addView(fields);
        TextView title = new TextView(this);
        title.setText("Vardiya Mobil");
        title.setTextSize(26);
        title.setTextColor(Color.rgb(22, 61, 50));
        fields.addView(title);
        TextView guidance = new TextView(this);
        guidance.setText(message.isEmpty() ? "Windows uygulamasının yerel HTTPS adresini girin." : message);
        guidance.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        guidance.setPadding(0, dp(8), 0, dp(12));
        fields.addView(guidance);
        EditText address = new EditText(this);
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setSingleLine(true);
        address.setHint("https://vardiya.ornek:8443");
        address.setContentDescription("Windows HTTPS adresi");
        address.setText(origin);
        fields.addView(address);
        Button open = new Button(this);
        open.setText("Bağlan");
        fields.addView(open);
        open.setOnClickListener(view -> {
            String proposed = normalize(address.getText().toString());
            if (proposed.isEmpty()) {
                guidance.setText("Yalnız geçerli bir HTTPS sunucu adresi girin; adresin sonuna yol eklemeyin.");
                return;
            }
            if (!proposed.equals(origin)) CookieManager.getInstance().removeAllCookies(null);
            origin = proposed;
            getSharedPreferences(PREFERENCES, MODE_PRIVATE).edit().putString("origin", origin).apply();
            connect();
        });
    }

    private static String normalize(String text) {
        try {
            URI value = URI.create(text.trim());
            if (!"https".equalsIgnoreCase(value.getScheme()) || value.getHost() == null
                    || value.getRawUserInfo() != null || value.getRawQuery() != null || value.getRawFragment() != null
                    || !Objects.equals(value.getRawPath(), "") && !Objects.equals(value.getRawPath(), "/")
                    || value.getPort() == 0 || value.getPort() > 65535) return "";
            int port = value.getPort();
            return "https://" + value.getHost().toLowerCase(Locale.ROOT) + (port == -1 || port == 443 ? "" : ":" + port);
        } catch (IllegalArgumentException ignored) {
            return "";
        }
    }

    private boolean sameOrigin(Uri uri) {
        Uri expected = Uri.parse(origin);
        return "https".equals(uri.getScheme()) && Objects.equals(expected.getHost(), uri.getHost())
                && (expected.getPort() == -1 ? 443 : expected.getPort()) == (uri.getPort() == -1 ? 443 : uri.getPort())
                && uri.getUserInfo() == null;
    }

    private void failed(WebView view, String message) {
        view.post(() -> {
            if (view == browser) connectionScreen(message);
        });
    }

    private void connect() {
        LinearLayout outer = frame();
        Button settings = new Button(this);
        settings.setText("Bağlantı ayarları");
        settings.setOnClickListener(view -> connectionScreen(""));
        outer.addView(settings, new LinearLayout.LayoutParams(-1, dp(48)));
        WebView view = new WebView(this);
        browser = view;
        view.getSettings().setJavaScriptEnabled(false);
        view.getSettings().setAllowFileAccess(false);
        view.getSettings().setAllowContentAccess(false);
        view.getSettings().setDomStorageEnabled(false);
        view.getSettings().setCacheMode(android.webkit.WebSettings.LOAD_NO_CACHE);
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, false);
        view.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView w, WebResourceRequest request) {
                return !sameOrigin(request.getUrl());
            }

            @Override public void onReceivedSslError(WebView w, SslErrorHandler handler, SslError error) {
                handler.cancel();
                failed(w, "Sunucu sertifikası doğrulanamadı. Adresi ve Android’deki güvenilen sertifikayı kontrol edin.");
            }

            @Override public void onReceivedError(WebView w, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) failed(w, "Windows sunucusuna ulaşılamadı. Aynı ağa bağlanıp yeniden deneyin.");
            }
        });
        outer.addView(view, new LinearLayout.LayoutParams(-1, 0, 1));
        view.loadUrl(origin + "/");
    }

    @Override protected void onDestroy() {
        if (browser != null) {
            container.removeView(browser);
            browser.destroy();
            browser = null;
        }
        super.onDestroy();
    }
}
