{ config, pkgs, lib, ... }: {
  options.cleanups.map = with lib;
    with types;
    let
      notionDb = strMatching "[a-f0-9]{32}";
      loglevel = enum [ "DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL" ];
    in {
      enable = mkEnableOption "Map generation";
      package = mkOption { type = package; };
      loglevel = mkOption {
        type = loglevel;
        default = "INFO";
      };
      trashDb = mkOption { type = notionDb; };
      mapCenter.lat = mkOption { type = float; };
      mapCenter.lon = mkOption { type = float; };
      mapSize = mkOption {
        type = int;
        default = 13;
      };
      mapName = mkOption {
        type = str;
        default = "map.html";
      };
      s3Bucket = mkOption { type = str; };
      s3BucketEndpoint = mkOption { type = str; };
      notionStaticPageUrl = mkOption { type = str; };

      interval = mkOption {
        type = str;
        default = "minutely";
      };

      secretsFile = mkOption { type = path; };
    };

  config = with config.cleanups.map;
    lib.mkIf enable {
      systemd.services.cleanups-map = {
        wantedBy = [ "multi-user.target" ];
        environment = {
          LOGLEVEL = loglevel;
          TRASH_DB_ID = trashDb;
          S3_BUCKET = s3Bucket;
          S3_BUCKET_ENDPOINT = s3BucketEndpoint;
          MAP_CENTER = "${toString mapCenter.lat},${toString mapCenter.lon}";
          MAP_SIZE = toString mapSize;
          MAP_NAME = mapName;
          NOTION_STATIC_PAGE_URL = notionStaticPageUrl;
        };
        serviceConfig = {
          ExecStart = "${package}/bin/cleanups-map";
          EnvironmentFile = secretsFile;
          Type = "oneshot";
        };
      };
      systemd.timers.cleanups-map = {
        timerConfig = {
          OnCalendar = interval;
          Unit = "cleanups-map.service";
        };
      };
    };
}
