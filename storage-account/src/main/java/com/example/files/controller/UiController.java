package com.example.files.controller;

import com.example.files.config.StorageAuthProperties;
import com.example.files.security.AuthFilter;
import com.example.files.service.StorageService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.servlet.mvc.support.RedirectAttributes;

import java.io.IOException;
import java.util.List;

@Controller
public class UiController {
    private final StorageService storageService;
    private final StorageAuthProperties authProperties;

    public UiController(StorageService storageService, StorageAuthProperties authProperties) {
        this.storageService = storageService;
        this.authProperties = authProperties;
    }

    @GetMapping("/")
    public String home() {
        return "redirect:/ui";
    }

    @GetMapping("/login")
    public String loginPage(HttpSession session) {
        if (session != null && Boolean.TRUE.equals(session.getAttribute(AuthFilter.SESSION_AUTHENTICATED))) {
            return "redirect:/ui";
        }
        return "login";
    }

    @PostMapping("/login")
    public String doLogin(@RequestParam("accessKey") String accessKey,
                          @RequestParam("secret") String secret,
                          HttpServletRequest request,
                          RedirectAttributes redirectAttributes) {
        boolean valid = accessKey.equals(authProperties.getAccessKey()) && secret.equals(authProperties.getSecret());
        if (!valid) {
            redirectAttributes.addFlashAttribute("error", "Invalid access key or secret.");
            return "redirect:/login";
        }

        HttpSession session = request.getSession(true);
        session.setAttribute(AuthFilter.SESSION_AUTHENTICATED, true);
        return "redirect:/ui";
    }

    @PostMapping("/logout")
    public String logout(HttpServletRequest request) {
        HttpSession session = request.getSession(false);
        if (session != null) {
            session.invalidate();
        }
        return "redirect:/login";
    }

    @GetMapping("/ui")
    public String explorer(@RequestParam(value = "path", defaultValue = "") String path,
                           Model model,
                           RedirectAttributes redirectAttributes) {
        try {
            String cleanPath = storageService.cleanFolder(path);
            List<StorageService.StorageItem> items = storageService.list(cleanPath);
            model.addAttribute("currentPath", cleanPath);
            model.addAttribute("parentPath", storageService.parentFolder(cleanPath));
            model.addAttribute("items", items);
            return "explorer";
        } catch (Exception e) {
            redirectAttributes.addFlashAttribute("error", "Unable to open folder: " + e.getMessage());
            return "redirect:/ui";
        }
    }

    @PostMapping("/ui/folder")
    public String createFolder(@RequestParam(value = "path", defaultValue = "") String path,
                               @RequestParam("name") String folderName,
                               RedirectAttributes redirectAttributes) {
        try {
            String cleanPath = storageService.cleanFolder(path);
            String cleanName = storageService.cleanFolder(folderName);
            if (cleanName.isBlank()) {
                redirectAttributes.addFlashAttribute("error", "Folder name cannot be empty.");
                return "redirect:/ui?path=" + cleanPath;
            }
            String fullPath = cleanPath.isEmpty() ? cleanName : cleanPath + "/" + cleanName;
            boolean created = storageService.createFolder(fullPath);
            if (!created) {
                redirectAttributes.addFlashAttribute("error", "Folder already exists.");
            } else {
                redirectAttributes.addFlashAttribute("success", "Folder created.");
            }
            return "redirect:/ui?path=" + cleanPath;
        } catch (Exception e) {
            redirectAttributes.addFlashAttribute("error", "Unable to create folder: " + e.getMessage());
            return "redirect:/ui?path=" + storageService.cleanFolder(path);
        }
    }

    @PostMapping("/ui/upload")
    public String uploadFile(@RequestParam(value = "path", defaultValue = "") String path,
                             @RequestParam("file") MultipartFile file,
                             RedirectAttributes redirectAttributes) {
        String cleanPath = storageService.cleanFolder(path);
        try {
            storageService.storeFile(cleanPath, file);
            redirectAttributes.addFlashAttribute("success", "File uploaded.");
        } catch (IllegalStateException e) {
            redirectAttributes.addFlashAttribute("error", e.getMessage());
        } catch (IllegalArgumentException e) {
            redirectAttributes.addFlashAttribute("error", e.getMessage());
        } catch (IOException e) {
            redirectAttributes.addFlashAttribute("error", "Unable to upload file: " + e.getMessage());
        }
        return "redirect:/ui?path=" + cleanPath;
    }

    @PostMapping("/ui/delete")
    public String deleteAny(@RequestParam(value = "path", defaultValue = "") String path,
                            @RequestParam("target") String target,
                            RedirectAttributes redirectAttributes) {
        String cleanPath = storageService.cleanFolder(path);
        try {
            String fullTarget = cleanPath.isEmpty() ? target : cleanPath + "/" + target;
            storageService.deleteAny(storageService.resolveRelativePath(fullTarget));
            redirectAttributes.addFlashAttribute("success", "Deleted successfully.");
        } catch (Exception e) {
            redirectAttributes.addFlashAttribute("error", "Unable to delete: " + e.getMessage());
        }
        return "redirect:/ui?path=" + cleanPath;
    }
}
