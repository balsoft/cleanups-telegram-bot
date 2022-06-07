{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };
  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        p = pkgs.python3.pkgs;
        notion-client = p.buildPythonPackage rec {
          pname = "notion-client";
          version = "1.0.0";
          propagatedBuildInputs = [ p.httpx ];
          checkInputs = [ p.pytest ];
          src = p.fetchPypi {
            inherit pname version;
            sha256 = "sha256-KBs3oVEqvsy4E3fUo0YYM/HB+9DOtuaB44t81Ea/G84=";
          };
        };

        gmplot = p.buildPythonPackage rec {
          pname = "gmplot";
          version = "1.4.1";
          postPatch = "touch requirements.txt";
          checkInputs = [ p.pytest p.requests ];
          src = p.fetchPypi {
            inherit pname version;
            sha256 = "sha256-z+ctJRwXtcBQQxadEhqXKFVL9luMlnYM6fq7bSacZmc=";
          };
        };

        python3 = pkgs.python3.withPackages (_:
          with p; [
            notion-client
            boto3
            python-telegram-bot
            pyyaml
            gmplot
            requests
          ]);

        bot = pkgs.writeShellScriptBin "cleanups-telegram-bot"
          "${python3}/bin/python3 ${./main.py}";
        map = pkgs.writeShellScriptBin "cleanups-map"
          "${python3}/bin/python3 ${./map.py}";

      in {
        packages = {
          inherit bot map;
          default = bot;
        };

        devShells.default =
          pkgs.mkShell { buildInputs = with p; [ python3 black pylint ]; };

        checks.formatting =
          pkgs.runCommand "check-formatting" { } "black --check ${./.}";
      }) // {
        nixosModules.bot = import ./modules/bot.nix;
        nixosModules.map = import ./modules/map.nix;
      };
}
