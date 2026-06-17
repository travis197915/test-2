package com.example.files.security;

import com.example.files.config.StorageAuthProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpSession;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Component
public class AuthFilter extends OncePerRequestFilter {
    public static final String SESSION_AUTHENTICATED = "storage_authenticated";
    private final StorageAuthProperties authProperties;

    public AuthFilter(StorageAuthProperties authProperties) {
        this.authProperties = authProperties;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        String path = request.getRequestURI();
        String contextPath = request.getContextPath();
        if (!contextPath.isEmpty() && path.startsWith(contextPath)) {
            path = path.substring(contextPath.length());
        }
        path = normalizePath(path);

        if (isPublicPath(path)) {
            filterChain.doFilter(request, response);
            return;
        }

        boolean isSessionAuthenticated = isSessionAuthenticated(request.getSession(false));
        boolean isApiPath = path.startsWith("/api/");
        boolean isUiPath = path.equals("/") || path.startsWith("/ui");

        if (isSessionAuthenticated || hasValidHeaderCredentials(request)) {
            filterChain.doFilter(request, response);
            return;
        }

        if (isApiPath) {
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.getWriter().write("{\"error\":\"Unauthorized\",\"message\":\"Provide valid access key and secret\"}");
            return;
        }

        if (isUiPath) {
            response.sendRedirect(request.getContextPath() + "/login");
            return;
        }

        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
    }

    private boolean isPublicPath(String path) {
        return path.equals("/login")
                || path.equals("/login/")
                || path.equals("/logout")
                || path.equals("/error")
                || path.startsWith("/favicon");
    }

    private String normalizePath(String rawPath) {
        if (rawPath == null || rawPath.isBlank()) {
            return "/";
        }
        int matrixIdx = rawPath.indexOf(';');
        String normalized = matrixIdx >= 0 ? rawPath.substring(0, matrixIdx) : rawPath;
        return normalized.isBlank() ? "/" : normalized;
    }

    private boolean isSessionAuthenticated(HttpSession session) {
        return session != null && Boolean.TRUE.equals(session.getAttribute(SESSION_AUTHENTICATED));
    }

    private boolean hasValidHeaderCredentials(HttpServletRequest request) {
        String key = request.getHeader("X-Storage-Key");
        String secret = request.getHeader("X-Storage-Secret");
        return safeEquals(authProperties.getAccessKey(), key) && safeEquals(authProperties.getSecret(), secret);
    }

    private boolean safeEquals(String expected, String actual) {
        return expected != null && !expected.isBlank() && expected.equals(actual);
    }
}
