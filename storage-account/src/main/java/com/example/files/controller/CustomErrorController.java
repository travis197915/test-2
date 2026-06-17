package com.example.files.controller;

import jakarta.servlet.RequestDispatcher;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.boot.web.servlet.error.ErrorController;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.RequestMapping;

@Controller
public class CustomErrorController implements ErrorController {

    @RequestMapping("/error")
    public String handleError(HttpServletRequest request, Model model) {

        Object status = request.getAttribute(RequestDispatcher.ERROR_STATUS_CODE);

        String title = "Document Not Available";
        String message = "The requested document could not be found in the system at this time.";

        if (status != null) {
            int statusCode = Integer.parseInt(status.toString());

            if (statusCode == HttpStatus.NOT_FOUND.value()) {
                message = "The document may have been moved, deleted, or the link is incorrect.";
            } else if (statusCode == HttpStatus.FORBIDDEN.value()) {
                message = "You do not have permission to access this document.";
            } else if (statusCode == HttpStatus.INTERNAL_SERVER_ERROR.value()) {
                message = "The system encountered an internal error. Please try again later.";
            }
        }

        model.addAttribute("title", title);
        model.addAttribute("message", message);

        return "error/document-not-available";
    }
}

