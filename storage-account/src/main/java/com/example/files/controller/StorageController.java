package com.example.files.controller;

import com.example.files.service.StorageService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.core.io.InputStreamResource;
import org.springframework.core.io.Resource;
import org.springframework.http.*;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.*;
import java.net.URLConnection;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;


@RestController
@RequestMapping("/api/storage")
public class StorageController {
    private final StorageService storageService;

    public StorageController(StorageService storageService) {
        this.storageService = storageService;
    }

    /* ===================== CREATE FOLDER ===================== */

    @PostMapping("/folder")
    public ResponseEntity<?> createFolder(@RequestParam("folder") String folder) throws IOException {
        String cleanFolder = storageService.cleanFolder(folder);
        if (cleanFolder.isEmpty() || cleanFolder.equals(".")) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "Invalid path",
                    "message", "Folder path cannot be empty"
            ));
        }
        try {
            boolean created = storageService.createFolder(cleanFolder);
            return ResponseEntity
                    .status(created ? HttpStatus.CREATED : HttpStatus.CONFLICT)
                    .body(Map.of(
                            "folder", cleanFolder,
                            "message", created ? "Folder created" : "Folder already exists",
                            "path", "/api/storage/" + cleanFolder
                    ));
        } catch (SecurityException e) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "Invalid path",
                    "message", "Invalid folder path"
            ));
        }
    }

    /* ===================== UPLOAD (POST) / VIEW or DOWNLOAD (GET) – single route ===================== */

    @PostMapping("")
    public ResponseEntity<?> upload(
            @RequestParam("folder") String folder,
            @RequestParam("file") MultipartFile file) throws IOException {

        if (file.getSize() > storageService.getMaxFileSize()) {
            return ResponseEntity
                    .status(HttpStatus.PAYLOAD_TOO_LARGE)
                    .body(Map.of(
                            "error", "File too large",
                            "message", "File size exceeds 50MB limit"
                    ));
        }

        String cleanFolder = storageService.cleanFolder(folder);
        try {
            String fileName = storageService.storeFile(cleanFolder, file);
            String filePath = cleanFolder.isEmpty() ? fileName : cleanFolder + "/" + fileName;
            return ResponseEntity.ok(Map.of(
                    "fileName", fileName,
                    "folder", cleanFolder,
                    "url", "/api/storage/" + filePath,
                    "viewUrl", "/api/storage/" + filePath,
                    "downloadUrl", "/api/storage/" + filePath + "?download=true"
            ));
        } catch (SecurityException e) {
            return ResponseEntity.badRequest().body(Map.of(
                    "error", "Invalid path",
                    "message", "Invalid folder path"
            ));
        } catch (IllegalStateException e) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of(
                    "error", "File already exists",
                    "message", e.getMessage()
            ));
        }
    }

    /**
     * Single GET route: view (inline) or download (attachment).
     * View: GET /api/storage/<folder>/<fileName>
     * Download: GET /api/storage/<folder>/<fileName>?download=true
     */
    @GetMapping("/**")
    public ResponseEntity<?> getFile(HttpServletRequest request) throws IOException {
        try {
            Path filePath = storageService.resolveRequestPath(request.getRequestURI(), "/api/storage/");
            if (!Files.exists(filePath)) {
                return notFound(filePath);
            }
            if (Files.isDirectory(filePath)) {
                return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(Map.of(
                        "error", "Path is a directory",
                        "message", "The requested path is a directory, not a file",
                        "path", filePath.toString()
                ));
            }

            String mimeType = URLConnection.guessContentTypeFromName(filePath.toString());
            if (mimeType == null) {
                mimeType = MediaType.APPLICATION_OCTET_STREAM_VALUE;
            }

            boolean download = "true".equalsIgnoreCase(request.getParameter("download"));
            String disposition = download ? "attachment" : "inline";

            Resource resource = storageService.loadFile(filePath);

            return ResponseEntity.ok()
                    .contentType(MediaType.parseMediaType(mimeType))
                    .header(HttpHeaders.CONTENT_DISPOSITION,
                            disposition + "; filename=\"" + filePath.getFileName() + "\"")
                    .body(resource);
        } catch (SecurityException e) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(Map.of(
                    "error", "Access denied",
                    "message", "Invalid or unauthorized path"
            ));
        }
    }

    /* ===================== DELETE FILE (same route: DELETE /api/storage/<path>) ===================== */

    @DeleteMapping("/**")
    public ResponseEntity<?> deleteFile(HttpServletRequest request) throws IOException {
        try {
            Path filePath = storageService.resolveRequestPath(request.getRequestURI(), "/api/storage/");
            if (Files.notExists(filePath)) {
                return notFound(filePath);
            }
            boolean isDirectory = Files.isDirectory(filePath);
            storageService.deleteAny(filePath);
            return ResponseEntity.ok(Map.of(
                    "message", isDirectory ? "Folder deleted" : "File deleted",
                    "path", filePath.getFileName().toString(),
                    "type", isDirectory ? "folder" : "file"
            ));
        } catch (SecurityException e) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(Map.of(
                    "error", "Access denied",
                    "message", "Invalid or unauthorized path"
            ));
        }
    }

    private ResponseEntity<?> notFound(String name) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of(
                "error", "File not found",
                "message", "File '" + name + "' does not exist",
                "resolvedPath", name
        ));
    }
    
    private ResponseEntity<?> notFound(Path filePath) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of(
                "error", "File not found",
                "message", "File does not exist at: " + filePath,
                "resolvedPath", filePath.toString()
        ));
    }
}

