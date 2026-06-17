package com.example.files.service;

import org.springframework.core.io.InputStreamResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;

@Service
public class StorageService {
    private static final long MAX_FILE_SIZE = 50L * 1024 * 1024; // 50MB
    private static final Path BASE_DIR = Paths.get("/data/uploads");

    public Path getBaseDir() {
        return BASE_DIR;
    }

    public long getMaxFileSize() {
        return MAX_FILE_SIZE;
    }

    public String cleanFolder(String folder) {
        String clean = StringUtils.cleanPath(folder == null ? "" : folder).replace("\\", "/");
        if (clean.startsWith("/")) {
            clean = clean.substring(1);
        }
        if (clean.equals(".")) {
            return "";
        }
        return clean;
    }

    public Path resolveFolderPath(String folder) {
        String cleanFolder = cleanFolder(folder);
        Path targetDir = BASE_DIR.resolve(cleanFolder).normalize();
        validateInsideBaseDir(targetDir);
        return targetDir;
    }

    public Path resolveRelativePath(String relativePath) {
        String clean = StringUtils.cleanPath(relativePath == null ? "" : relativePath).replace("\\", "/");
        if (clean.startsWith("/")) {
            clean = clean.substring(1);
        }
        Path path = BASE_DIR.resolve(clean).normalize();
        validateInsideBaseDir(path);
        return path;
    }

    public Path resolveRequestPath(String requestUri, String prefix) {
        int prefixIndex = requestUri.indexOf(prefix);
        if (prefixIndex == -1) {
            throw new SecurityException("Prefix not found in URI: " + requestUri);
        }
        String path = requestUri.substring(prefixIndex + prefix.length());
        int queryIndex = path.indexOf('?');
        if (queryIndex != -1) {
            path = path.substring(0, queryIndex);
        }
        path = URLDecoder.decode(path, StandardCharsets.UTF_8);
        return resolveRelativePath(path);
    }

    public boolean createFolder(String folder) throws IOException {
        Path targetDir = resolveFolderPath(folder);
        boolean created = Files.notExists(targetDir);
        Files.createDirectories(targetDir);
        return created;
    }

    public String storeFile(String folder, MultipartFile file) throws IOException {
        if (file.getSize() > MAX_FILE_SIZE) {
            throw new IllegalArgumentException("File size exceeds 50MB limit");
        }
        String fileName = StringUtils.cleanPath(Objects.requireNonNull(file.getOriginalFilename()));
        if (fileName.isBlank()) {
            throw new IllegalArgumentException("File name cannot be empty");
        }

        Path targetDir = resolveFolderPath(folder);
        Files.createDirectories(targetDir);
        Path targetFile = targetDir.resolve(fileName).normalize();
        validateInsideBaseDir(targetFile);

        if (Files.exists(targetFile)) {
            throw new IllegalStateException("A file named '" + fileName + "' already exists in this folder.");
        }

        Files.copy(file.getInputStream(), targetFile);
        return fileName;
    }

    public Resource loadFile(Path filePath) throws IOException {
        return new InputStreamResource(Files.newInputStream(filePath));
    }

    public boolean deleteAny(Path path) throws IOException {
        if (Files.notExists(path)) {
            return false;
        }
        if (Files.isDirectory(path)) {
            try (var stream = Files.walk(path)) {
                stream.sorted(Comparator.reverseOrder()).forEach(p -> {
                    try {
                        Files.delete(p);
                    } catch (IOException e) {
                        throw new RuntimeException(e);
                    }
                });
            } catch (RuntimeException ex) {
                if (ex.getCause() instanceof IOException ioException) {
                    throw ioException;
                }
                throw ex;
            }
            return true;
        }
        Files.delete(path);
        return true;
    }

    public List<StorageItem> list(String folder) throws IOException {
        Path dir = resolveFolderPath(folder);
        Files.createDirectories(dir);
        List<StorageItem> items = new ArrayList<>();
        try (var stream = Files.list(dir)) {
            stream.sorted(Comparator.comparing(p -> p.getFileName().toString().toLowerCase()))
                    .forEach(path -> {
                        try {
                            boolean isDirectory = Files.isDirectory(path);
                            long size = isDirectory ? 0L : Files.size(path);
                            String relative = BASE_DIR.relativize(path).toString().replace("\\", "/");
                            items.add(new StorageItem(path.getFileName().toString(), relative, isDirectory, size));
                        } catch (IOException e) {
                            throw new RuntimeException(e);
                        }
                    });
        } catch (RuntimeException ex) {
            if (ex.getCause() instanceof IOException ioException) {
                throw ioException;
            }
            throw ex;
        }
        return items;
    }

    public String parentFolder(String folder) {
        String clean = cleanFolder(folder);
        int idx = clean.lastIndexOf('/');
        if (idx <= 0) {
            return "";
        }
        return clean.substring(0, idx);
    }

    private void validateInsideBaseDir(Path path) {
        if (!path.startsWith(BASE_DIR)) {
            throw new SecurityException("Path traversal detected: " + path);
        }
    }

    public record StorageItem(String name, String relativePath, boolean directory, long size) {
    }
}
