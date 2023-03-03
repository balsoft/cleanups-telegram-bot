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

        bot = pkgs.writeShellScriptBin "cleanups-telegram-bot"
          "exec ${python3}/bin/python3 ${./main.py}";
        map = pkgs.writeShellScriptBin "cleanups-map"
          "exec ${python3}/bin/python3 ${./map.py}";

      in {
        packages = {
          inherit bot map;
          default = bot;
        };

        devShells.default =
          pkgs.mkShell { buildInputs = [ python3 pkgs.python3.pkgs.pylsp-mypy pkgs.black pkgs.ffmpeg ]; };

        checks.formatting =
          pkgs.runCommand "check-formatting" { } "black --check ${./.}";
      }) // {
        nixosModules.bot = import ./modules/bot.nix;
        nixosModules.map = import ./modules/map.nix;
      };
}
