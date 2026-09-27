package com.interactivedigits.affiliartracker;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;

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

        FrameLayout root = new FrameLayout(this);

        webView = new WebView(this);
        webView.setLayoutParams(new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));
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
        root.addView(webView);

        Button refreshButton = new Button(this);
        refreshButton.setText("Refresh");
        refreshButton.setTextColor(Color.WHITE);
        refreshButton.setBackgroundColor(Color.rgb(37, 99, 235));
        refreshButton.setAllCaps(false);

        FrameLayout.LayoutParams buttonParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(48)
        );
        buttonParams.gravity = Gravity.END | Gravity.BOTTOM;
        buttonParams.setMargins(dp(16), dp(16), dp(16), dp(20));
        refreshButton.setLayoutParams(buttonParams);

        refreshButton.setOnClickListener(v -> {
            webView.getSettings().setCacheMode(WebSettings.LOAD_NO_CACHE);
            webView.clearCache(true);
            webView.reload();
        });

        root.addView(refreshButton);
        setContentView(root);

        if (savedInstanceState == null) {
            webView.loadUrl(DASHBOARD_URL);
        } else {
            webView.restoreState(savedInstanceState);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.getSettings().setCacheMode(WebSettings.LOAD_NO_CACHE);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        webView.saveState(outState);
        super.onSaveInstanceState(outState);
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
            webView.destroy();
        }
        super.onDestroy();
    }
}
