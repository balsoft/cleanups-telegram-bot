{ config, pkgs, lib, ... }: {
  options.cleanups.telegram-bot = with lib;
    with types;
    let
      notionDb = strMatching "[a-f0-9]{32}";
      loglevel = enum [ "DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL" ];
    in {
      enable =
        mkEnableOption "A telegram bot to receive reports about dirty places";
      package = mkOption { type = package; };
      loglevel = mkOption {
        type = loglevel;
        default = "INFO";
      };
      trashDb = mkOption { type = notionDb; };
      preferencesDb = mkOption {
        type = nullOr notionDb;
        default = null;
      };
      s3Bucket = mkOption { type = str; };
      s3BucketEndpoint = mkOption { type = str; };
      dataPathPrefix = mkOption {
        type = path;
        default = "/tmp";
      };
      translationsDb = mkOption { type = notionDb; };
      languages = mkOption { type = nullOr (listOf str); default = null; };

      secretsFile = mkOption { type = path; };
    };
  config = let cfg = config.cleanups.telegram-bot;
  in {
    systemd.services.cleanups-telegram-bot = with cfg;
      lib.mkIf enable {
        preStart = "mkdir ${dataPathPrefix}/{tmpf,dynamic}";
        script = "cleanups-telegram-bot";
        wantedBy = [ "multi-user.target" ];
        path = [ package ];
        environment = {
          LOGLEVEL = loglevel;
          TRASH_DB_ID = trashDb;
          S3_BUCKET = s3Bucket;
          S3_BUCKET_ENDPOINT = s3BucketEndpoint;
          DATA_PATH_PREFIX = dataPathPrefix;
          TRANSLATIONS_DB_ID = translationsDb;
        } // lib.optionalAttrs (!isNull preferencesDb) {
          PREFERENCES_DB_ID = preferencesDb;
        } // lib.optionalAttrs (!isNull languages) {
          LANGUAGES = builtins.concatStringsSep "," languages;
        };
        serviceConfig = {
          EnvironmentFile = secretsFile;
          PrivateTmp = true;
        };
      };
  };
}
