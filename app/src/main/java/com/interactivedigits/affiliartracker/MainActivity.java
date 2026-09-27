package com.interactivedigits.affiliartracker;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    private static final String DASHBOARD_URL =
            "https://affiliar-tracker-production.up.railway.app/";
    private WebView webView;

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().setStatusBarColor(Color.rgb(11, 16, 32));
        getWindow().setNavigationBarColor(Color.rgb(11, 16, 32));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(11, 16, 32));

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.setPadding(dp(14), dp(8), dp(10), dp(8));
        toolbar.setBackgroundColor(Color.rgb(22, 29, 44));

        TextView title = new TextView(this);
        title.setText("Affiliar Tracker");
        title.setTextColor(Color.WHITE);
        title.setTextSize(18);
        title.setGravity(android.view.Gravity.CENTER_VERTICAL);

        LinearLayout.LayoutParams titleParams =
                new LinearLayout.LayoutParams(0, dp(52), 1f);
        toolbar.addView(title, titleParams);

        Button refreshButton = new Button(this);
        refreshButton.setText("REFRESH");
        refreshButton.setTextColor(Color.WHITE);
        refreshButton.setTextSize(14);
        refreshButton.setAllCaps(false);
        refreshButton.setBackgroundColor(Color.rgb(37, 99, 235));

        LinearLayout.LayoutParams refreshParams =
                new LinearLayout.LayoutParams(dp(110), dp(52));
        toolbar.addView(refreshButton, refreshParams);

        root.addView(toolbar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(11, 16, 32));

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setUserAgentString(
                settings.getUserAgentString() + " AffiliarTrackerAndroid/1.1"
        );

        webView.setWebViewClient(new WebViewClient());

        root.addView(webView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f
        ));

        setContentView(root);

        refreshButton.setOnClickListener(v -> {
            webView.stopLoading();
            webView.clearCache(true);
            webView.getSettings().setCacheMode(WebSettings.LOAD_NO_CACHE);

            String current = webView.getUrl();
            if (current == null || current.isEmpty()) {
                current = DASHBOARD_URL;
            }
            String separator = current.contains("?") ? "&" : "?";
            webView.loadUrl(current + separator + "_refresh=" + System.currentTimeMillis());
        });

        webView.loadUrl(DASHBOARD_URL + "?_appv=2");
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.getSettings().setCacheMode(WebSettings.LOAD_NO_CACHE);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.clearCache(true);
            webView.destroy();
        }
        super.onDestroy();
    }
}
