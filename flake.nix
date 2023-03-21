{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    mach-nix.url = "github:davhau/mach-nix";
  };
  outputs = { self, nixpkgs, flake-utils, mach-nix }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python3 = mach-nix.lib.${system}.mkPython {
          requirements = builtins.readFile ./requirements.txt;
        };

        bot = pkgs.writeShellScriptBin "cleanups-telegram-bot" ''
          export TRANSLATIONS_YAML=${./translations.yaml}
          export PATH="$PATH:${pkgs.lib.makeBinPath [ pkgs.ffmpeg ]}"
          exec ${python3}/bin/python3 ${./main.py}
        '';
      in {
        packages = {
          inherit bot;
          default = bot;
        };

        devShells.default = pkgs.mkShell {
          buildInputs =
            [ python3 pkgs.python3.pkgs.pylsp-mypy pkgs.black pkgs.ffmpeg ];
        };

        checks.formatting =
          pkgs.runCommand "check-formatting" { } "black --check ${./.}";
      }) // {
        nixosModules.bot = import ./modules/bot.nix;
      };
}
