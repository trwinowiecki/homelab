{
  description = "Homelab workstation/CI tooling (ansible, sops, age, linters)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      devShells = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [
              age
              sops
              ansible
              ansible-lint
              python3
              shellcheck
              yamllint
              gitleaks
              git
              gh
              jq
              docker-compose
            ];
            shellHook = ''
              export SOPS_AGE_KEY_FILE="''${SOPS_AGE_KEY_FILE:-$PWD/.age/workstation.key}"
              echo "homelab dev shell: sops=$(sops --version 2>/dev/null | head -1)"
            '';
          };
        });
    };
}
