## Install 

Build tools.

```sh
sudo apt install ninja-build flex bison
```

Package manager vcpkg.

```sh
git clone https://github.com/microsoft/vcpkg
```

Add vcpkg to your profile.

```sh
export VCPKG_ROOT='~/workspace/vcpkg'
export PATH="$VCPKG_ROOT:$PATH"
```

