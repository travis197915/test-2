# 1) BUILD STAGE
FROM maven:3.9-eclipse-temurin-17 AS build

WORKDIR /app

COPY pom.xml .
RUN mvn dependency:go-offline

COPY src ./src
RUN mvn clean package -DskipTests


# 2) RUNTIME STAGE (Tomcat 10.1.52)
FROM tomcat:10.1.52-jdk17-temurin

RUN mkdir -p /data/uploads \
    && chmod -R 777 /data/uploads

VOLUME ["/data/uploads"]

RUN rm -rf /usr/local/tomcat/webapps/*

COPY --from=build /app/target/storage.war /usr/local/tomcat/webapps/

# Friendly root landing page to guide users to the app login.
RUN mkdir -p /usr/local/tomcat/webapps/ROOT \
    && printf '%s\n' \
    '<!doctype html>' \
    '<html><head><meta charset="utf-8"><title>Storage Account</title>' \
    '<meta http-equiv="refresh" content="0; url=/storage/login">' \
    '</head><body style="font-family:Arial,sans-serif;padding:24px;">' \
    '<h2>Storage account is running</h2>' \
    '<p>Open <a href="/storage/login">/storage/login</a> to continue.</p>' \
    '</body></html>' \
    > /usr/local/tomcat/webapps/ROOT/index.html

# Expose Tomcat port
EXPOSE 8080

# Start Tomcat
CMD ["catalina.sh", "run"]
