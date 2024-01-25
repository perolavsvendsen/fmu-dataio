FROM nginxinc/nginx-unprivileged:alpine

ARG uriprefix
ENV URIPREFIX=${uriprefix}

WORKDIR /app
USER 0

RUN apk update
RUN apk upgrade
RUN apk add jq

COPY schema/definitions definitions/
RUN chown -R 101:101 .

COPY schema/app/install-schema-files.sh /docker-entrypoint.d/50-install-schema-files.sh
RUN chmod +x /docker-entrypoint.d/50-install-schema-files.sh

COPY schema/app/fmu-schemas.conf /etc/nginx/conf.d

RUN rm /etc/nginx/conf.d/default.conf

# See https://radix.equinor.com/docs/topic-docker/#running-as-non-root
RUN addgroup -S -g 1001 radix-non-root-group
RUN adduser -S -u 1001 -G radix-non-root-group radix-non-root-user
USER 1001

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
