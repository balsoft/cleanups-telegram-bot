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
      s3Bucket = mkOption { type = str; };
      s3BucketEndpoint = mkOption { type = str; };
      dataPathPrefix = mkOption {
        type = path;
        default = "/tmp";
      };
      languages = mkOption {
        type = nullOr (listOf str);
        default = null;
      };

      firebaseSDKKeyPath = mkOption {
        type = path;
      };

      firebaseProjectId = mkOption {
        type = str;
      };

      secretsFile = mkOption { type = path; };
    };
  config = let cfg = config.cleanups.telegram-bot;
  in {
    systemd.services.cleanups-telegram-bot = with cfg;
      lib.mkIf enable {
        preStart = "mkdir ${dataPathPrefix}/{tmpf,dynamic}";
        wantedBy = [ "multi-user.target" ];
        environment = {
          LOGLEVEL = loglevel;
          S3_BUCKET = s3Bucket;
          S3_BUCKET_ENDPOINT = s3BucketEndpoint;
          DATA_PATH_PREFIX = dataPathPrefix;
          FIREBASE_SERVICE_ACCOUNT_KEY_PATH = firebaseSDKKeyPath;
          FIREBASE_PROJECT_ID = firebaseProjectId;
        };
        serviceConfig = {
          ExecStart = "${package}/bin/cleanups-telegram-bot";
          EnvironmentFile = secretsFile;
          PrivateTmp = true;
        };
      };
  };
}
